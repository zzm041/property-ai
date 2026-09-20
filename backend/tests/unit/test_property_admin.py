"""Property archive management, import, and history policy tests."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, IamErrorCode
from app.domain.audit import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.dev_seed import SEED_PERMISSIONS, SEED_ROLE_PERMISSIONS
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.property import Building, House, Property, Unit
from app.model.resident import ResidentHouseRelation
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.schema.property import HouseImportRow
from app.service.property_admin import PropertyAdminService
from app.service.resident import ResidentBindingService


def _principal(*permissions: str, property_id: int = 10) -> Principal:
    return Principal(
        user_id=7,
        status="ACTIVE",
        display_name="Property Manager",
        grants=(
            RoleGrant(
                role_code="PROPERTY_ADMIN",
                scope=OrganizationScope(
                    ScopeType.PROPERTY, tenant_id=1, property_id=property_id
                ),
                permissions=frozenset(permissions),
            ),
        ),
    )


def _property_service(actor: Principal):
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    repository = MagicMock(spec=PropertyRepository)
    audit = MagicMock()
    service = PropertyAdminService(
        session,
        repository=repository,
        audit_service=audit,
        principal_loader=lambda user_id: actor,
    )
    return service, session, repository, audit


def test_frozen_admin_roles_receive_property_manage_permission() -> None:
    permission_codes = {item.code for item in SEED_PERMISSIONS}

    assert "property:manage" in permission_codes
    assert "property:manage" in SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]
    assert "property:manage" in SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"]
    assert "property:manage" not in SEED_ROLE_PERMISSIONS["SUPERVISOR"]
    assert "property:manage" not in SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"]


def test_property_archive_audit_actions_have_fixed_resources() -> None:
    expected = {
        AuditAction.PROPERTY_BUILDING_CREATED: AuditResourceType.BUILDING,
        AuditAction.PROPERTY_UNIT_CREATED: AuditResourceType.UNIT,
        AuditAction.PROPERTY_HOUSE_CREATED: AuditResourceType.HOUSE,
        AuditAction.PROPERTY_HOUSE_UPDATED: AuditResourceType.HOUSE,
    }

    for action, resource_type in expected.items():
        assert AUDIT_ACTION_POLICIES[action].resource_type is resource_type


def test_list_archive_is_scoped_by_property_read() -> None:
    actor = _principal("property:read")
    service, _, repository, _ = _property_service(actor)
    repository.get_property.return_value = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    repository.list_all_buildings.return_value = []
    repository.list_all_units.return_value = []
    repository.list_all_houses.return_value = []

    assert service.list_archive(actor=actor, tenant_id=1, property_id=10) == (
        (),
        (),
        (),
    )
    repository.list_all_houses.assert_called_once_with(tenant_id=1, property_id=10)


def test_property_admin_cannot_cross_property_scope() -> None:
    actor = _principal("property:read", "property:manage", property_id=10)
    service, _, repository, _ = _property_service(actor)
    repository.get_property.return_value = Property(
        id=11,
        tenant_id=1,
        name="Other",
        address="Address",
        timezone="Asia/Shanghai",
    )

    with pytest.raises(AppError) as captured:
        service.list_archive(actor=actor, tenant_id=1, property_id=11)

    assert captured.value.code == IamErrorCode.FORBIDDEN
    repository.list_all_houses.assert_not_called()


def test_atomic_import_reuses_existing_parents_and_is_retry_safe(monkeypatch) -> None:
    actor = _principal("property:read", "property:manage")
    service, _, repository, audit = _property_service(actor)
    property_record = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    building = Building(id=20, tenant_id=1, property_id=10, code="B1", name="1号楼")
    unit = Unit(
        id=30,
        tenant_id=1,
        property_id=10,
        building_id=20,
        code="U1",
        name="1单元",
    )
    house = House(
        id=40,
        tenant_id=1,
        property_id=10,
        unit_id=30,
        room_no="101",
        floor=1,
        status="ACTIVE",
    )
    repository.get_property.return_value = property_record
    repository.get_building_by_code.return_value = building
    repository.get_unit_by_code.return_value = unit
    repository.get_house_by_room.return_value = house
    monkeypatch.setattr(
        "app.core.database.run_owned_transaction", lambda session, operation: operation()
    )

    result = service.import_houses(
        actor=actor,
        tenant_id=1,
        property_id=10,
        rows=(
            HouseImportRow(
                building_code="B1",
                building_name="1号楼",
                unit_code="U1",
                unit_name="1单元",
                room_no="101",
                floor=1,
                status="ACTIVE",
            ),
        ),
        request_id="property-import-retry",
    )

    assert result.created_buildings == 0
    assert result.created_units == 0
    assert result.created_houses == 0
    assert result.unchanged_houses == 1
    audit.append.assert_not_called()


def test_import_rejects_existing_house_attribute_conflict(monkeypatch) -> None:
    actor = _principal("property:manage")
    service, _, repository, _ = _property_service(actor)
    repository.get_property.return_value = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    repository.get_building_by_code.return_value = Building(
        id=20, tenant_id=1, property_id=10, code="B1", name="1号楼"
    )
    repository.get_unit_by_code.return_value = Unit(
        id=30,
        tenant_id=1,
        property_id=10,
        building_id=20,
        code="U1",
        name="1单元",
    )
    repository.get_house_by_room.return_value = House(
        id=40,
        tenant_id=1,
        property_id=10,
        unit_id=30,
        room_no="101",
        floor=2,
        status="ACTIVE",
    )
    monkeypatch.setattr(
        "app.core.database.run_owned_transaction", lambda session, operation: operation()
    )

    with pytest.raises(AppError) as captured:
        service.import_houses(
            actor=actor,
            tenant_id=1,
            property_id=10,
            rows=(
                HouseImportRow(
                    building_code="B1",
                    building_name="1号楼",
                    unit_code="U1",
                    unit_name="1单元",
                    room_no="101",
                    floor=1,
                    status="ACTIVE",
                ),
            ),
            request_id="property-import-conflict",
        )

    assert captured.value.code == "RES.HIERARCHY_CONFLICT"
    assert captured.value.details == {"row": 1, "field": "house"}


def test_relation_history_requires_relation_read_permission() -> None:
    actor = _principal("property:manage")
    session = MagicMock()
    repository = MagicMock(spec=ResidentBindingRepository)
    property_repository = MagicMock(spec=PropertyRepository)
    property_repository.get_property.return_value = Property(
        id=10,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    service = ResidentBindingService(
        session,
        repository=repository,
        property_repository=property_repository,
        principal_loader=lambda user_id: actor,
    )

    with pytest.raises(AppError) as captured:
        service.list_property_relation_history(
            actor=actor, tenant_id=1, property_id=10
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    repository.list_relation_history_for_property.assert_not_called()


def test_relation_end_locks_row_and_preserves_history(monkeypatch) -> None:
    actor = _principal("resident_binding:review", "resident_relation:read")
    session = MagicMock()
    repository = MagicMock(spec=ResidentBindingRepository)
    relation = ResidentHouseRelation(
        id=50,
        user_id=8,
        tenant_id=1,
        property_id=10,
        house_id=40,
        relation_type="OWNER",
        status="ACTIVE",
        valid_from=datetime(2026, 9, 20, tzinfo=UTC),
        valid_to=None,
        bind_request_id=60,
    )
    repository.get_relation_for_update.return_value = relation
    repository.get_house_in_scope.return_value = House(
        id=40,
        tenant_id=1,
        property_id=10,
        unit_id=30,
        room_no="101",
        floor=1,
        status="ACTIVE",
    )
    repository.end_relation.side_effect = lambda record, ended_at, status: (
        setattr(record, "valid_to", ended_at)
        or setattr(record, "status", status)
        or record
    )
    audit = MagicMock()
    service = ResidentBindingService(
        session,
        repository=repository,
        property_repository=MagicMock(spec=PropertyRepository),
        audit_service=audit,
        principal_loader=lambda user_id: actor,
    )
    monkeypatch.setattr(
        "app.core.database.run_owned_transaction", lambda db, operation: operation()
    )

    ended = service.end_relation_by_id(
        actor=actor,
        relation_id=50,
        status="ENDED",
        request_id="relation-end",
    )

    assert ended is relation
    assert ended.status == "ENDED"
    assert ended.valid_to is not None
    repository.get_relation_for_update.assert_called_once_with(50)
    repository.add_relation.assert_not_called()
    audit.append.assert_called_once()
