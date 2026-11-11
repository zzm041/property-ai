"""SLA policy administration scope, concurrency, audit, and page wiring."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import mysql

from app.core.errors import AppError, IamErrorCode, SlaErrorCode
from app.domain.audit import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.work_order import SLA_POLICY_MANAGE, sla_warning_lead_minutes
from app.domain.dev_seed import SEED_ROLE_PERMISSIONS
from app.repository.work_order import WorkOrderRepository
from app.repository.property import PropertyRepository
from app.schema.sla import PutSlaPolicyRequest
from app.service.audit import AuditService
from app.service.sla_policy import SlaPolicyService


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _grant(
    role_code: str,
    scope_type: ScopeType,
    *,
    tenant_id: int = 1,
    property_id: int | None = None,
    permissions: tuple[str, ...] = (SLA_POLICY_MANAGE,),
) -> RoleGrant:
    return RoleGrant(
        role_code=role_code,
        scope=OrganizationScope(
            scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
        ),
        permissions=frozenset(permissions),
    )


def _actor(*grants: RoleGrant) -> Principal:
    return Principal(
        user_id=7,
        status="ACTIVE",
        display_name="Synthetic SLA manager",
        grants=tuple(grants),
    )


def _property_admin(*, property_id: int = 10, tenant_id: int = 1) -> Principal:
    return _actor(
        _grant(
            "PROPERTY_ADMIN",
            ScopeType.PROPERTY,
            tenant_id=tenant_id,
            property_id=property_id,
        )
    )


def _tenant_admin(*, tenant_id: int = 1) -> Principal:
    return _actor(
        _grant("TENANT_ADMIN", ScopeType.TENANT, tenant_id=tenant_id)
    )


def _policy(
    *,
    policy_id: int = 51,
    tenant_id: int = 1,
    property_id: int | None = 10,
    category: str = "PLUMBING",
    priority: str = "MEDIUM",
    respond_minutes: int = 30,
    complete_minutes: int = 120,
    status: str = "ACTIVE",
    updated_at: datetime = NOW,
):
    return SimpleNamespace(
        id=policy_id,
        tenant_id=tenant_id,
        property_id=property_id,
        category=category,
        priority=priority,
        respond_minutes=respond_minutes,
        complete_minutes=complete_minutes,
        status=status,
        updated_at=updated_at,
    )


def _service(actor: Principal) -> SlaPolicyService:
    session = MagicMock()
    session.info = {}
    session.in_transaction.return_value = False
    service = SlaPolicyService.__new__(SlaPolicyService)
    service._session = session
    service._repository = MagicMock()
    service._properties = MagicMock()
    service._audit = MagicMock()
    service._principal_loader = lambda _user_id: actor
    service._properties.get_tenant.return_value = SimpleNamespace(
        id=1,
        status="ACTIVE",
    )
    service._properties.get_tenant_for_update.return_value = SimpleNamespace(
        id=1,
        status="ACTIVE",
    )
    service._properties.get_property_by_id.return_value = SimpleNamespace(
        id=10,
        tenant_id=1,
    )
    service._properties.get_property_by_id_for_update.return_value = SimpleNamespace(
        id=10,
        tenant_id=1,
    )
    return service


def _put(service: SlaPolicyService, actor: Principal, **overrides):
    values = {
        "actor": actor,
        "tenant_id": 1,
        "property_id": 10,
        "category": "PLUMBING",
        "priority": "MEDIUM",
        "respond_minutes": 45,
        "complete_minutes": 180,
        "status": "ACTIVE",
        "expected_updated_at": NOW,
        "request_id": "sla-policy-test",
    }
    values.update(overrides)
    return service.put_policy(**values)


def test_property_admin_can_update_only_its_property_override() -> None:
    actor = _property_admin()
    service = _service(actor)
    record = _policy()
    service._repository.get_sla_policy_for_update.return_value = record
    service._repository.flush_sla_policy.side_effect = lambda item: item

    result = _put(service, actor)

    assert result.respond_minutes == 45
    service._audit.append.assert_called_once()
    audit = service._audit.append.call_args.kwargs
    assert audit["action"] is AuditAction.SLA_POLICY_CHANGED
    assert audit["resource_type"] is AuditResourceType.SLA_POLICY
    assert audit["scope_type"] is ScopeType.PROPERTY
    assert audit["tenant_id"] == 1
    assert audit["property_id"] == 10
    assert audit["details"]["before"]["respond_minutes"] == 30
    assert audit["details"]["after"]["respond_minutes"] == 45

    with pytest.raises(AppError) as captured:
        _put(service, actor, property_id=None)
    assert captured.value.code == IamErrorCode.FORBIDDEN


def test_seed_sla_permission_matches_frozen_role_matrix() -> None:
    assert SLA_POLICY_MANAGE in SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"]
    assert SLA_POLICY_MANAGE in SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]
    for role in ("SUPERVISOR", "STAFF", "RESIDENT", "SYSTEM_ADMIN"):
        assert SLA_POLICY_MANAGE not in SEED_ROLE_PERMISSIONS[role]


def test_tenant_admin_can_create_tenant_default() -> None:
    actor = _tenant_admin()
    service = _service(actor)
    service._repository.get_sla_policy_for_update.return_value = None

    def add(record):
        record.id = 52
        record.created_at = NOW
        record.updated_at = NOW
        return record

    service._repository.add_sla_policy.side_effect = add

    result = _put(
        service,
        actor,
        property_id=None,
        expected_updated_at=None,
    )

    assert result.id == 52
    assert result.property_id is None
    assert service._audit.append.call_args.kwargs["scope_type"] is ScopeType.TENANT


def test_tenant_default_cannot_be_disabled() -> None:
    actor = _tenant_admin()
    service = _service(actor)

    with pytest.raises(AppError) as captured:
        _put(
            service,
            actor,
            property_id=None,
            status="DISABLED",
            expected_updated_at=None,
        )

    assert captured.value.code == SlaErrorCode.INVALID_INPUT
    service._repository.get_sla_policy_for_update.assert_not_called()
    service._audit.append.assert_not_called()


def test_mixed_role_cannot_apply_other_tenant_grant() -> None:
    actor = _actor(
        _grant(
            "PROPERTY_ADMIN",
            ScopeType.PROPERTY,
            tenant_id=1,
            property_id=10,
        ),
        _grant("TENANT_ADMIN", ScopeType.TENANT, tenant_id=2),
    )
    service = _service(actor)
    service._properties.get_property_by_id_for_update.return_value = SimpleNamespace(
        id=20,
        tenant_id=2,
    )
    service._properties.get_tenant.return_value = SimpleNamespace(
        id=2,
        status="ACTIVE",
    )

    with pytest.raises(AppError) as captured:
        _put(service, actor, tenant_id=1, property_id=20)

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._repository.get_sla_policy_for_update.assert_not_called()


def test_list_rejects_claimed_tenant_mismatch_before_policy_query() -> None:
    actor = _property_admin()
    service = _service(actor)

    with pytest.raises(AppError) as captured:
        service.list_for_property(
            actor=actor,
            property_id=10,
            claimed_tenant_id=2,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._repository.list_sla_policies_for_property.assert_not_called()


def test_supervisor_without_property_manage_is_denied() -> None:
    actor = _actor(
        _grant(
            "SUPERVISOR",
            ScopeType.PROPERTY,
            property_id=10,
            permissions=("work_order:dispatch",),
        )
    )
    service = _service(actor)

    with pytest.raises(AppError) as captured:
        _put(service, actor)

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._repository.get_sla_policy_for_update.assert_not_called()


def test_same_value_replay_is_noop_without_concurrency_token() -> None:
    actor = _property_admin()
    service = _service(actor)
    record = _policy(respond_minutes=45, complete_minutes=180)
    service._repository.get_sla_policy_for_update.return_value = record

    assert _put(service, actor, expected_updated_at=None) is record
    service._repository.flush_sla_policy.assert_not_called()
    service._audit.append.assert_not_called()


def test_versioned_update_closes_previous_version_and_records_reason() -> None:
    actor = _property_admin()
    service = _service(actor)
    record = _policy()
    previous = SimpleNamespace(
        id=80,
        version_no=2,
        accept_minutes=20,
        respond_minutes=30,
        complete_minutes=120,
        warning_lead_minutes=30,
        escalation_minutes=0,
        status="ACTIVE",
        effective_to=None,
    )
    service._repository.get_sla_policy_for_update.return_value = record
    service._repository.flush_sla_policy.side_effect = lambda item: item
    service._versioning_available = MagicMock(return_value=True)
    service._latest_version = MagicMock(return_value=previous)
    service._session.scalar.return_value = 2

    result = _put(
        service,
        actor,
        accept_minutes=15,
        warning_lead_minutes=20,
        escalation_minutes=5,
        change_reason="调整本小区响应窗口",
    )

    assert result is record
    assert previous.effective_to is not None
    created = service._session.add.call_args.args[0]
    assert created.version_no == 3
    assert created.change_reason == "调整本小区响应窗口"
    assert created.effective_from == previous.effective_to
    assert created.effective_to is None


def test_versioned_change_requires_reason_but_exact_replay_does_not() -> None:
    actor = _property_admin()
    service = _service(actor)
    record = _policy(respond_minutes=45, complete_minutes=180)
    same = SimpleNamespace(
        id=80,
        version_no=2,
        accept_minutes=45,
        respond_minutes=45,
        complete_minutes=180,
        warning_lead_minutes=45,
        escalation_minutes=0,
        status="ACTIVE",
        effective_to=None,
    )
    service._repository.get_sla_policy_for_update.return_value = record
    service._versioning_available = MagicMock(return_value=True)
    service._latest_version = MagicMock(return_value=same)

    assert _put(service, actor, expected_updated_at=None) is record

    same.respond_minutes = 30
    with pytest.raises(AppError) as captured:
        _put(service, actor, expected_updated_at=NOW)
    assert captured.value.code == SlaErrorCode.INVALID_INPUT


def test_version_only_change_advances_legacy_concurrency_token() -> None:
    actor = _property_admin()
    service = _service(actor)
    record = _policy(respond_minutes=45, complete_minutes=180)
    previous = SimpleNamespace(
        id=80,
        version_no=2,
        accept_minutes=45,
        respond_minutes=45,
        complete_minutes=180,
        warning_lead_minutes=45,
        escalation_minutes=0,
        status="ACTIVE",
        effective_to=None,
    )
    service._repository.get_sla_policy_for_update.return_value = record
    service._versioning_available = MagicMock(return_value=True)
    service._latest_version = MagicMock(return_value=previous)
    service._session.scalar.return_value = 2

    original_token = record.updated_at
    result = _put(
        service,
        actor,
        expected_updated_at=original_token,
        warning_lead_minutes=30,
        change_reason="调整预警提前量",
    )

    assert result.updated_at > original_token
    assert result._latest_version.warning_lead_minutes == 30

    with pytest.raises(AppError) as captured:
        _put(
            service,
            actor,
            expected_updated_at=original_token,
            warning_lead_minutes=20,
            change_reason="再次调整预警提前量",
        )
    assert captured.value.code == SlaErrorCode.CONFLICT


def test_stale_different_update_returns_stable_conflict_without_audit() -> None:
    actor = _property_admin()
    service = _service(actor)
    service._repository.get_sla_policy_for_update.return_value = _policy(
        updated_at=NOW + timedelta(seconds=1)
    )

    with pytest.raises(AppError) as captured:
        _put(service, actor)

    assert captured.value.code == SlaErrorCode.CONFLICT
    assert captured.value.status_code == 409
    assert captured.value.details == {"retryable": True}
    service._repository.flush_sla_policy.assert_not_called()
    service._audit.append.assert_not_called()


def test_response_window_cannot_exceed_completion_window_in_service() -> None:
    actor = _property_admin()
    service = _service(actor)

    with pytest.raises(AppError) as captured:
        _put(
            service,
            actor,
            respond_minutes=121,
            complete_minutes=120,
        )

    assert captured.value.code == SlaErrorCode.INVALID_INPUT
    service._repository.get_sla_policy_for_update.assert_not_called()
    service._audit.append.assert_not_called()


def test_list_returns_property_tenant_and_code_default_resolution() -> None:
    actor = _property_admin()
    service = _service(actor)
    property_active = _policy(category="PLUMBING", priority="MEDIUM")
    tenant_active = _policy(
        policy_id=52,
        property_id=None,
        category="ELECTRICAL",
        priority="HIGH",
    )
    tenant_disabled = _policy(
        policy_id=53,
        property_id=None,
        category="HVAC",
        priority="LOW",
        status="DISABLED",
    )
    service._repository.list_sla_policies_for_property.return_value = (
        property_active,
        tenant_active,
        tenant_disabled,
    )

    listing = service.list_for_property(
        actor=actor,
        property_id=10,
        claimed_tenant_id=1,
    )

    resolved = {
        (item.category, item.priority): item for item in listing.effective
    }
    assert len(resolved) == 24
    assert resolved[("PLUMBING", "MEDIUM")].source == "PROPERTY"
    assert resolved[("ELECTRICAL", "HIGH")].source == "TENANT"
    assert resolved[("HVAC", "LOW")].source == "CODE_DEFAULT"


def test_repository_list_and_update_queries_are_scope_bound_and_locked() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = ()
    repository = WorkOrderRepository(session)

    assert repository.list_sla_policies_for_property(
        tenant_id=1,
        property_id=10,
    ) == ()
    list_sql = str(
        session.scalars.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "sla_policies.tenant_id = %s" in list_sql
    assert "sla_policies.property_id = %s" in list_sql
    assert "sla_policies.property_id IS NULL" in list_sql

    session.scalar.return_value = None
    repository.get_sla_policy_for_update(
        tenant_id=1,
        property_id=10,
        category="PLUMBING",
        priority="MEDIUM",
    )
    update_sql = str(
        session.scalar.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "sla_policies.tenant_id = %s" in update_sql
    assert "sla_policies.property_id = %s" in update_sql
    assert "FOR UPDATE" in update_sql


def test_parent_scope_queries_are_locked_for_first_create_serialization() -> None:
    session = MagicMock()
    repository = PropertyRepository(session)

    repository.get_tenant_for_update(1)
    tenant_sql = str(
        session.scalar.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "FROM tenants" in tenant_sql
    assert "FOR UPDATE" in tenant_sql

    repository.get_property_by_id_for_update(10)
    property_sql = str(
        session.scalar.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "FROM properties" in property_sql
    assert "FOR UPDATE" in property_sql


def test_real_audit_validator_accepts_sla_change_snapshot() -> None:
    repository = MagicMock()
    repository.append.side_effect = lambda record: record
    service = AuditService(MagicMock(), repository=repository)

    record = service.append(
        action=AuditAction.SLA_POLICY_CHANGED,
        resource_type=AuditResourceType.SLA_POLICY,
        actor_user_id=7,
        resource_id=51,
        request_id="sla-audit-test",
        scope_type=ScopeType.PROPERTY,
        tenant_id=1,
        property_id=10,
        details={
            "scope_type": "PROPERTY",
            "property_id": 10,
            "category": "PLUMBING",
            "priority": "MEDIUM",
            "before": {
                "respond_minutes": 30,
                "complete_minutes": 120,
                "status": "ACTIVE",
            },
            "after": {
                "respond_minutes": 45,
                "complete_minutes": 180,
                "status": "ACTIVE",
            },
        },
    )

    assert record.action == AuditAction.SLA_POLICY_CHANGED.value
    assert record.resource_type == AuditResourceType.SLA_POLICY.value
    assert record.details_json["after"]["respond_minutes"] == 45


def test_schema_rejects_bool_extra_and_out_of_range_minutes() -> None:
    valid = {
        "tenant_id": 1,
        "property_id": 10,
        "category": "PLUMBING",
        "priority": "MEDIUM",
        "respond_minutes": 30,
        "complete_minutes": 120,
        "status": "ACTIVE",
    }
    for field, value in (
        ("respond_minutes", True),
        ("complete_minutes", 0),
        ("complete_minutes", 2_147_483_648),
        ("respond_minutes", 121),
    ):
        with pytest.raises(ValidationError):
            candidate = {**valid, field: value}
            if field == "respond_minutes" and value == 121:
                candidate["complete_minutes"] = 120
            PutSlaPolicyRequest.model_validate(candidate)
    with pytest.raises(ValidationError):
        PutSlaPolicyRequest.model_validate({**valid, "unexpected": "x"})


def test_short_completion_window_never_schedules_warning_before_creation() -> None:
    assert sla_warning_lead_minutes(1) == 1
    assert sla_warning_lead_minutes(14) == 14
    assert sla_warning_lead_minutes(60) == 15
    with pytest.raises(ValueError):
        sla_warning_lead_minutes(0)


def test_audit_policy_and_miniprogram_wiring_are_explicit() -> None:
    policy = AUDIT_ACTION_POLICIES[AuditAction.SLA_POLICY_CHANGED]
    assert policy.resource_type is AuditResourceType.SLA_POLICY
    assert policy.allowed_scope_types == {ScopeType.TENANT, ScopeType.PROPERTY}

    page = (
        REPO_ROOT / "miniprogram/pages/supervisor/sla.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/supervisor/sla.wxml"
    ).read_text(encoding="utf-8")
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    home = (REPO_ROOT / "miniprogram/pages/home/home.wxml").read_text(
        encoding="utf-8"
    )
    app_json = (REPO_ROOT / "miniprogram/app.json").read_text(encoding="utf-8")
    work_order_service = (
        REPO_ROOT / "backend/app/service/work_order/lifecycle.py"
    ).read_text(encoding="utf-8")

    assert 'indexOf("sla:manage")' in page
    assert "expected_updated_at" in page
    assert 'effective.source === "CODE_DEFAULT"' in page
    assert "return this.fetchPolicies()" in page
    assert "接单 / 响应共用时限" in template
    assert "禁用小区覆盖会回退企业默认" in template
    assert "当前固定升级行为（不可在本页编辑）" in template
    assert 'path: "/admin/sla-policies", method: "PUT"' in api
    assert "SLA 策略管理" in home
    assert '"pages/supervisor/sla"' in app_json
    assert "sla_warning_lead_minutes(complete_minutes)" in work_order_service
