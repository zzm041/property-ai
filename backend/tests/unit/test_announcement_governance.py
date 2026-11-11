"""Announcement targeting, review, edit-history, and visibility policy tests."""

from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.core.errors import AnnouncementErrorCode, AppError, IamErrorCode
from app.domain.ai import ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH, ANNOUNCEMENT_READ
from app.domain.governance import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.ai import Announcement, AnnouncementScope
from app.model.property import Building, House, Property, Unit
from app.model.resident import ResidentHouseRelation
from app.repository.ai import AiRepository
from app.repository.audit import AuditRepository
from app.repository.employee import EmployeeRepository
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.announcement import AnnouncementService


NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def _principal(
    *permissions: str,
    scope_type: ScopeType = ScopeType.PROPERTY,
    role_code: str = "PROPERTY_ADMIN",
) -> Principal:
    return Principal(
        user_id=7,
        status="ACTIVE",
        display_name="Manager",
        grants=(
            RoleGrant(
                role_code=role_code,
                scope=OrganizationScope(
                    scope_type,
                    tenant_id=1,
                    property_id=10 if scope_type is ScopeType.PROPERTY else None,
                ),
                permissions=frozenset(permissions),
            ),
        ),
    )


def _service() -> tuple[AnnouncementService, MagicMock]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    service = AnnouncementService(
        session,
        settings=MagicMock(),
        llm=MagicMock(),
        clock=lambda: NOW,
    )
    service._ai = MagicMock(spec=AiRepository)
    service._audit_repository = MagicMock(spec=AuditRepository)
    service._employees = MagicMock(spec=EmployeeRepository)
    service._properties = MagicMock(spec=PropertyRepository)
    service._residents = MagicMock(spec=ResidentBindingRepository)
    service._work_orders = MagicMock(spec=WorkOrderRepository)
    service._audit = MagicMock()
    return service, session


def _announcement(status: str = "DRAFT") -> Announcement:
    return Announcement(
        id=31,
        tenant_id=1,
        property_id=10,
        incident_id=None,
        title="Original",
        content="Original content",
        status=status,
        ai_draft=0,
        created_by_user_id=7,
    )


def _scope(
    *,
    scope_type: str = "PROPERTY",
    building_id: int | None = None,
    unit_id: int | None = None,
    audience: str = "RESIDENT",
) -> AnnouncementScope:
    return AnnouncementScope(
        id=41,
        tenant_id=1,
        property_id=10,
        announcement_id=31,
        scope_type=scope_type,
        building_id=building_id,
        unit_id=unit_id,
        audience=audience,
    )


def test_announcement_audit_actions_preserve_edit_and_review_history() -> None:
    for action in (
        AuditAction.ANNOUNCEMENT_DRAFT_CREATED,
        AuditAction.ANNOUNCEMENT_UPDATED,
        AuditAction.ANNOUNCEMENT_SUBMITTED,
        AuditAction.ANNOUNCEMENT_REJECTED,
        AuditAction.ANNOUNCEMENT_PUBLISHED,
    ):
        assert AUDIT_ACTION_POLICIES[action].resource_type is AuditResourceType.ANNOUNCEMENT


def test_tenant_wide_target_requires_tenant_scope() -> None:
    service, _ = _service()
    property_actor = _principal(ANNOUNCEMENT_DRAFT)

    with pytest.raises(AppError) as captured:
        service._resolve_target(
            actor=property_actor,
            permission=ANNOUNCEMENT_DRAFT,
            tenant_id=1,
            property_id=10,
            scope_type="TENANT",
            building_id=None,
            unit_id=None,
            audience="RESIDENT",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN

    tenant_actor = _principal(
        ANNOUNCEMENT_DRAFT,
        scope_type=ScopeType.TENANT,
        role_code="TENANT_ADMIN",
    )
    target = service._resolve_target(
        actor=tenant_actor,
        permission=ANNOUNCEMENT_DRAFT,
        tenant_id=1,
        property_id=10,
        scope_type="TENANT",
        building_id=None,
        unit_id=None,
        audience="OWNER",
    )
    assert target.scope_type == "TENANT"
    assert target.audience == "OWNER"


def test_building_and_unit_targets_must_belong_to_property() -> None:
    service, _ = _service()
    actor = _principal(ANNOUNCEMENT_DRAFT)
    service._properties.get_building.return_value = Building(
        id=20,
        tenant_id=1,
        property_id=10,
        code="B1",
        name="Building",
    )
    service._properties.get_unit.return_value = Unit(
        id=30,
        tenant_id=1,
        property_id=10,
        building_id=20,
        code="U1",
        name="Unit",
    )

    building = service._resolve_target(
        actor=actor,
        permission=ANNOUNCEMENT_DRAFT,
        tenant_id=1,
        property_id=10,
        scope_type="BUILDING",
        building_id=20,
        unit_id=None,
        audience="RESIDENT",
    )
    unit = service._resolve_target(
        actor=actor,
        permission=ANNOUNCEMENT_DRAFT,
        tenant_id=1,
        property_id=10,
        scope_type="UNIT",
        building_id=20,
        unit_id=30,
        audience="TENANT",
    )

    assert building.building_id == 20
    assert unit.unit_id == 30
    with pytest.raises(AppError) as captured:
        service._resolve_target(
            actor=actor,
            permission=ANNOUNCEMENT_DRAFT,
            tenant_id=1,
            property_id=10,
            scope_type="UNIT",
            building_id=21,
            unit_id=30,
            audience="RESIDENT",
        )
    assert captured.value.code == AnnouncementErrorCode.SCOPE_MISMATCH


def test_rejected_announcement_can_be_edited_without_losing_review_history(
    monkeypatch,
) -> None:
    service, _ = _service()
    actor = _principal(ANNOUNCEMENT_DRAFT)
    record = _announcement("REJECTED")
    service._ai.get_announcement_for_update.return_value = record
    service._ai.list_announcement_scopes.return_value = (_scope(),)
    monkeypatch.setattr(
        "app.service.announcement.begin_owned_transaction",
        lambda session: nullcontext(),
    )

    result = service.update_draft(
        actor=actor,
        announcement_id=31,
        title="Revised title",
        content="Revised content",
        scope_type="AUDIENCE",
        building_id=None,
        unit_id=None,
        audience="OWNER",
        request_id="announcement-edit",
    )

    assert result.status == "DRAFT"
    assert result.title == "Revised title"
    replacement = service._ai.replace_announcement_scope.call_args.kwargs["scope"]
    assert replacement.scope_type == "AUDIENCE"
    assert replacement.audience == "OWNER"
    assert service._audit.append.call_args.kwargs["action"] is AuditAction.ANNOUNCEMENT_UPDATED


def test_rejection_requires_reason_and_appends_review_and_audit(monkeypatch) -> None:
    service, _ = _service()
    actor = _principal(ANNOUNCEMENT_PUBLISH)
    record = _announcement("PENDING_REVIEW")
    service._ai.get_announcement_for_update.return_value = record
    service._ai.list_announcement_scopes.return_value = (_scope(),)
    monkeypatch.setattr(
        "app.service.announcement.begin_owned_transaction",
        lambda session: nullcontext(),
    )

    with pytest.raises(AppError) as captured:
        service.publish(
            actor=actor,
            announcement_id=31,
            request_id="announcement-reject-empty",
            decision="REJECT",
            reason=" ",
        )
    assert captured.value.code == AnnouncementErrorCode.INVALID_INPUT
    service._ai.add_announcement_review.assert_not_called()

    result = service.publish(
        actor=actor,
        announcement_id=31,
        request_id="announcement-reject",
        decision="REJECT",
        reason="范围需要缩小",
    )
    assert result.status == "REJECTED"
    review = service._ai.add_announcement_review.call_args.args[0]
    assert review.reason == "范围需要缩小"
    audit = service._audit.append.call_args.kwargs
    assert audit["action"] is AuditAction.ANNOUNCEMENT_REJECTED
    assert audit["details"]["reason"] == "范围需要缩小"


def test_existing_tenant_target_requires_tenant_scope_for_all_manage_actions() -> None:
    service, _ = _service()
    record = _announcement("PENDING_REVIEW")
    service._ai.get_announcement_for_update.return_value = record
    service._ai.list_announcement_scopes.return_value = (_scope(scope_type="TENANT"),)
    property_actor = _principal(ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH)

    for permission in (ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH):
        with pytest.raises(AppError) as captured:
            service._require_manage(property_actor, record.id, permission)
        assert captured.value.code == IamErrorCode.FORBIDDEN

    tenant_actor = _principal(
        ANNOUNCEMENT_DRAFT,
        ANNOUNCEMENT_PUBLISH,
        scope_type=ScopeType.TENANT,
        role_code="TENANT_ADMIN",
    )
    assert service._require_manage(tenant_actor, record.id, ANNOUNCEMENT_DRAFT) is record
    assert service._require_manage(tenant_actor, record.id, ANNOUNCEMENT_PUBLISH) is record


def test_management_list_pushes_actor_target_scope_into_repository_query() -> None:
    service, _ = _service()
    service._work_orders.get_property.return_value = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    service._ai.list_announcements_for_management.return_value = ()

    service.list_managed(
        actor=_principal(ANNOUNCEMENT_DRAFT),
        property_id=10,
        after_id=100,
        limit=25,
    )
    property_query = service._ai.list_announcements_for_management.call_args.kwargs
    assert property_query == {
        "tenant_id": 1,
        "property_id": 10,
        "include_tenant_targets": False,
        "include_property_targets": True,
        "after_id": 100,
        "limit": 25,
    }

    service.list_managed(
        actor=_principal(
            ANNOUNCEMENT_DRAFT,
            scope_type=ScopeType.TENANT,
            role_code="TENANT_ADMIN",
        ),
        property_id=10,
    )
    tenant_query = service._ai.list_announcements_for_management.call_args.kwargs
    assert tenant_query["include_tenant_targets"] is True
    assert tenant_query["include_property_targets"] is True


def test_management_repository_applies_target_filter_before_limit() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = AiRepository(session)

    assert (
        repository.list_announcements_for_management(
            tenant_id=1,
            property_id=10,
            include_tenant_targets=False,
            include_property_targets=False,
            limit=7,
        )
        == ()
    )
    session.scalars.assert_not_called()

    repository.list_announcements_for_management(
        tenant_id=1,
        property_id=10,
        include_tenant_targets=False,
        include_property_targets=True,
        after_id=20,
        limit=7,
    )
    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "announcements.property_id = 10" in sql
    assert "announcement_scopes.scope_type != 'TENANT'" in sql
    assert "announcements.id > 20" in sql
    assert "LIMIT 7" in sql


def test_resident_visibility_passes_house_and_audience_targets_to_repository() -> None:
    service, _ = _service()
    actor = _principal(ANNOUNCEMENT_READ, role_code="RESIDENT")
    service._work_orders.get_property.return_value = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    relation = ResidentHouseRelation(
        id=51,
        tenant_id=1,
        property_id=10,
        user_id=7,
        house_id=40,
        relation_type="OWNER",
        status="ACTIVE",
        valid_from=NOW,
    )
    house = House(
        id=40,
        tenant_id=1,
        property_id=10,
        unit_id=30,
        room_no="101",
        status="ACTIVE",
    )
    service._residents.list_active_relations_for_user.return_value = ((relation, house),)
    service._employees.get_employee_for_user.return_value = None
    service._properties.get_unit.return_value = Unit(
        id=30,
        tenant_id=1,
        property_id=10,
        building_id=20,
        code="U1",
        name="Unit",
    )
    service._ai.list_published_announcements.return_value = ()

    assert service.list_published(actor=actor, property_id=10) == ()
    query = service._ai.list_published_announcements.call_args.kwargs
    assert query["building_ids"] == (20,)
    assert query["unit_ids"] == (30,)
    assert query["audiences"] == ("OWNER", "RESIDENT")
