"""Atomic system-admin organization onboarding and client wiring."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.audit import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.identity import UserPhone
from app.model.property import Property, Tenant
from app.model.work_order import IdempotencyRecord
from app.repository.credential import CredentialRepository
from app.repository.property import PropertyRepository
from app.schema.organization import CreateOrganizationRequest
from app.service.audit import AuditService
from app.service.organization import OrganizationOnboardingService
from app.service.rbac_mutation import RbacMutationService

REPO_ROOT = Path(__file__).resolve().parents[3]


def _actor(*, role: str = "SYSTEM_ADMIN", status: str = "ACTIVE") -> Principal:
    return Principal(
        user_id=7,
        status=status,
        display_name="System operator",
        grants=(
            RoleGrant(
                role_code=role,
                scope=OrganizationScope(ScopeType.SYSTEM),
                permissions=frozenset({"role:manage"}),
            ),
        ),
    )


def _service(
    *, actor: Principal | None = None, phone: UserPhone | None = None
) -> tuple[
    OrganizationOnboardingService,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    transaction = MagicMock(is_active=True)
    session.get_transaction.return_value = transaction

    class _Begin:
        def __enter__(self):
            session.in_transaction.return_value = True
            return self

        def __exit__(self, exc_type, exc, tb):
            session.in_transaction.return_value = False
            return False

    session.begin.side_effect = _Begin
    properties = MagicMock(spec=PropertyRepository)
    credentials = MagicMock(spec=CredentialRepository)
    audit = MagicMock(spec=AuditService)
    rbac = MagicMock(spec=RbacMutationService)
    credentials.get_active_phone_for_update.return_value = phone or UserPhone(
        id=11,
        user_id=13,
        phone_e164="+8613800000000",
        status="ACTIVE",
    )

    def add_tenant(record: Tenant) -> Tenant:
        record.id = 101
        return record

    def add_property(record: Property) -> Property:
        record.id = 202
        return record

    properties.add_tenant.side_effect = add_tenant
    properties.add_property.side_effect = add_property
    properties.get_idempotency.return_value = None
    service = OrganizationOnboardingService(
        session,
        property_repository=properties,
        credential_repository=credentials,
        audit_service=audit,
        principal_loader=MagicMock(return_value=actor or _actor()),
        rbac_mutation=rbac,
    )
    return service, session, properties, audit, rbac


def _create(
    service: OrganizationOnboardingService,
    *,
    idempotency_key: str = "organization-onboarding-key-1",
    property_name: str = " Riverside Garden ",
):
    return service.create_organization(
        actor=_actor(),
        tenant_name=" Example Property Company ",
        tenant_contact=" 021-12345678 ",
        property_name=property_name,
        property_address=" 1 Test Road ",
        timezone="Asia/Shanghai",
        initial_admin_phone="+8613800000000",
        idempotency_key=idempotency_key,
        request_id="organization-onboarding-1",
    )


def test_onboarding_creates_scope_grant_and_audits_atomically() -> None:
    service, session, properties, audit, rbac = _service()

    result = _create(service)

    assert result.tenant.id == 101
    assert result.tenant.name == "Example Property Company"
    assert result.property_record.id == 202
    assert result.property_record.tenant_id == 101
    assert result.initial_admin_user_id == 13
    rbac._grant_user_role_in_current_transaction.assert_called_once_with(
        actor_user_id=7,
        target_user_id=13,
        role_code="TENANT_ADMIN",
        scope=OrganizationScope(ScopeType.TENANT, tenant_id=101),
        request_id="organization-onboarding-1",
    )
    assert [call.kwargs["action"] for call in audit.append.call_args_list] == [
        AuditAction.ORGANIZATION_TENANT_CREATED,
        AuditAction.ORGANIZATION_PROPERTY_CREATED,
    ]
    assert properties.add_tenant.call_count == 1
    assert properties.add_property.call_count == 1
    record = properties.add_idempotency.call_args.args[0]
    assert record.idempotency_key == "organization-onboarding-key-1"
    assert record.user_id == 7
    assert record.tenant_id == 101
    assert record.property_id == 202
    assert record.resource_type == "ORGANIZATION_ONBOARDING"
    assert record.resource_id == 13
    assert len(record.request_hash) == 64
    session.begin.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_onboarding_reloads_and_requires_actual_system_admin() -> None:
    service, _, properties, audit, rbac = _service(actor=_actor(role="OTHER"))

    with pytest.raises(AppError) as captured:
        _create(service)

    assert captured.value.status_code == 403
    properties.add_tenant.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()


def test_onboarding_requires_pre_registered_active_phone() -> None:
    service, _, properties, audit, rbac = _service()
    service._credentials.get_active_phone_for_update.return_value = None

    with pytest.raises(AppError) as captured:
        _create(service)

    assert captured.value.status_code == 404
    properties.add_tenant.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()


def test_onboarding_maps_unique_conflict_without_partial_success() -> None:
    service, _, properties, audit, rbac = _service()
    properties.add_tenant.side_effect = IntegrityError(
        "INSERT", {}, Exception(1062, "uq_tenants_name")
    )

    with pytest.raises(AppError) as captured:
        _create(service)

    assert captured.value.status_code == 409
    assert captured.value.code == "RES.DUPLICATE_RESOURCE"
    properties.add_property.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()


def test_onboarding_replays_same_key_without_duplicate_writes() -> None:
    service, session, properties, audit, rbac = _service()
    tenant = Tenant(id=101, name="Example Property Company", status="ACTIVE")
    property_record = Property(
        id=202,
        tenant_id=101,
        name="Riverside Garden",
        address="1 Test Road",
        timezone="Asia/Shanghai",
    )
    request_hash = service._request_hash(
        tenant_name="Example Property Company",
        tenant_contact="021-12345678",
        property_name="Riverside Garden",
        property_address="1 Test Road",
        timezone="Asia/Shanghai",
        initial_admin_phone="+8613800000000",
    )
    properties.get_idempotency.return_value = IdempotencyRecord(
        id=501,
        idempotency_key="organization-onboarding-key-1",
        user_id=7,
        tenant_id=101,
        property_id=202,
        resource_type="ORGANIZATION_ONBOARDING",
        resource_id=13,
        request_hash=request_hash,
    )
    properties.get_tenant.return_value = tenant
    properties.get_property.return_value = property_record

    result = _create(service)

    assert result.tenant is tenant
    assert result.property_record is property_record
    assert result.initial_admin_user_id == 13
    properties.add_tenant.assert_not_called()
    properties.add_property.assert_not_called()
    properties.add_idempotency.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()
    session.begin.assert_called_once()


def test_onboarding_rejects_same_key_with_changed_request() -> None:
    service, _, properties, audit, rbac = _service()
    properties.get_idempotency.return_value = IdempotencyRecord(
        id=501,
        idempotency_key="organization-onboarding-key-1",
        user_id=7,
        tenant_id=101,
        property_id=202,
        resource_type="ORGANIZATION_ONBOARDING",
        resource_id=13,
        request_hash="0" * 64,
    )

    with pytest.raises(AppError) as captured:
        _create(service, property_name="Different Property")

    assert captured.value.status_code == 409
    assert captured.value.code == "RES.IDEMPOTENCY_CONFLICT"
    properties.add_tenant.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()


@pytest.mark.parametrize("value", ["", " leading", "trailing ", "x" * 129])
def test_onboarding_rejects_invalid_idempotency_key(value: str) -> None:
    service, _, properties, audit, rbac = _service()

    with pytest.raises(AppError) as captured:
        _create(service, idempotency_key=value)

    assert captured.value.status_code == 422
    assert captured.value.code == "RES.IDEMPOTENCY_KEY_REQUIRED"
    properties.get_idempotency.assert_not_called()
    properties.add_tenant.assert_not_called()
    rbac._grant_user_role_in_current_transaction.assert_not_called()
    audit.append.assert_not_called()


def test_schema_normalizes_phone_and_rejects_invalid_value() -> None:
    request = CreateOrganizationRequest(
        tenant_name="Tenant",
        property_name="Property",
        property_address="Address",
        initial_admin_phone="13800000000",
    )
    assert request.initial_admin_phone == "+8613800000000"
    with pytest.raises(ValueError):
        CreateOrganizationRequest(
            tenant_name="Tenant",
            property_name="Property",
            property_address="Address",
            initial_admin_phone="not-a-phone",
        )


def test_organization_audit_contracts_and_miniprogram_wiring() -> None:
    tenant_policy = AUDIT_ACTION_POLICIES[AuditAction.ORGANIZATION_TENANT_CREATED]
    property_policy = AUDIT_ACTION_POLICIES[
        AuditAction.ORGANIZATION_PROPERTY_CREATED
    ]
    assert tenant_policy.resource_type is AuditResourceType.TENANT
    assert tenant_policy.allowed_scope_types == frozenset({ScopeType.TENANT})
    assert property_policy.resource_type is AuditResourceType.PROPERTY
    assert property_policy.allowed_scope_types == frozenset({ScopeType.PROPERTY})

    inventory = (REPO_ROOT / "miniprogram/app.json").read_text(encoding="utf-8")
    home = (REPO_ROOT / "miniprogram/pages/home/home.wxml").read_text(
        encoding="utf-8"
    )
    page = (REPO_ROOT / "miniprogram/pages/system/organizations.js").read_text(
        encoding="utf-8"
    )
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    assert '"pages/system/organizations"' in inventory
    assert "{{canBootstrapOrganization}}" in home
    assert 'path: "/system/organizations"' in api
    assert ".createOrganization(body, this._pendingSubmission.idempotencyKey)" in page
    assert "initial_admin_phone" in page
    assert "this._pendingSubmission" in page
    assert "fingerprint !== fingerprint" in page
    assert "this._pendingSubmission = null" in page
