"""RBAC Mutation Service authorization, errors, audit, and transaction tests."""

from inspect import signature
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import OWNED_TXN_KEY
from app.core.errors import AppError, IamErrorCode
from app.domain.audit import AuditAction, AuditResourceType, AuditSource
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.repository.property import PropertyRepository
from app.repository.rbac_mutation import RbacMutationRepository
from app.service.audit import AuditService
from app.service.rbac_mutation import RbacMutationService


def _principal(
    *,
    user_id: int = 1,
    status: str = "ACTIVE",
    scope: OrganizationScope | None = None,
    permissions: tuple[str, ...] = ("role:manage",),
) -> Principal:
    resolved_scope = scope or OrganizationScope(ScopeType.SYSTEM)
    return Principal(
        user_id=user_id,
        status=status,
        display_name="Actor",
        grants=(
            RoleGrant(
                role_code="SYSTEM_ADMIN",
                scope=resolved_scope,
                permissions=frozenset(permissions),
            ),
        ),
    )


def _build_service(
    *,
    current_actor: Principal | None = None,
) -> tuple[
    RbacMutationService,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    repository = MagicMock(spec=RbacMutationRepository)
    property_repository = MagicMock(spec=PropertyRepository)
    audit_service = MagicMock(spec=AuditService)
    principal_loader = MagicMock(
        return_value=current_actor or _principal()
    )
    service = RbacMutationService(
        session,
        repository=repository,
        property_repository=property_repository,
        audit_service=audit_service,
        principal_loader=principal_loader,
    )
    repository.get_user.return_value = User(
        id=20,
        status="ACTIVE",
        display_name="Target",
    )
    repository.get_role_by_code.return_value = Role(
        id=31,
        code="PROPERTY_ADMIN",
        name="Property Admin",
    )
    repository.get_permission_by_code.return_value = Permission(
        id=47,
        code="property:read",
        name="Read property",
    )
    repository.find_user_role.return_value = None
    repository.find_role_permission.return_value = None
    property_repository.get_tenant.return_value = object()
    property_repository.get_property.return_value = object()

    def add_user_role(record: UserRole) -> UserRole:
        record.id = 101
        return record

    def add_role_permission(record: RolePermission) -> RolePermission:
        record.id = 202
        return record

    repository.add_user_role.side_effect = add_user_role
    repository.add_role_permission.side_effect = add_role_permission
    return (
        service,
        session,
        repository,
        property_repository,
        audit_service,
        principal_loader,
    )


def _integrity_error(message: str) -> IntegrityError:
    return IntegrityError(
        "INSERT",
        {},
        Exception(1062, message),
    )


def test_public_grant_reloads_actor_and_writes_fixed_audit_metadata() -> None:
    current_actor = _principal()
    stale_actor = _principal(permissions=())
    service, session, repository, _, audit_service, principal_loader = (
        _build_service(current_actor=current_actor)
    )

    result = service.grant_user_role(
        actor=stale_actor,
        target_user_id=20,
        role_code="PROPERTY_ADMIN",
        scope=OrganizationScope(ScopeType.SYSTEM),
        request_id="c2-grant",
    )

    principal_loader.assert_called_once_with(stale_actor.user_id)
    repository.add_user_role.assert_called_once()
    added = repository.add_user_role.call_args.args[0]
    assert added.user_id == 20
    assert added.role_id == 31
    assert added.scope_type == "SYSTEM"
    assert result.relation_id == 101
    assert result.action is AuditAction.IAM_USER_ROLE_GRANTED
    assert result.resource_type is AuditResourceType.USER_ROLE
    audit = audit_service.append.call_args.kwargs
    assert audit["actor_user_id"] == current_actor.user_id
    assert audit["resource_id"] == 101
    assert audit["request_id"] == "c2-grant"
    assert audit["details"] == {
        "target_user_id": 20,
        "role_code": "PROPERTY_ADMIN",
        "scope_type": "SYSTEM",
        "source": "INTERNAL",
    }
    session.begin.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_public_methods_do_not_accept_forgeable_audit_or_actor_fields() -> None:
    for method in (
        RbacMutationService.grant_user_role,
        RbacMutationService.revoke_user_role,
        RbacMutationService.grant_role_permission,
        RbacMutationService.revoke_role_permission,
        RbacMutationService.grant_user_role_for_development_seed,
        RbacMutationService.grant_role_permission_for_development_seed,
    ):
        parameters = set(signature(method).parameters)
        assert "actor_user_id" not in parameters
        assert "source" not in parameters
        assert "details" not in parameters


def test_current_database_actor_is_authorization_source() -> None:
    stale_allowed = _principal()
    current_denied = _principal(permissions=())
    service, _, repository, property_repository, audit_service, _ = (
        _build_service(current_actor=current_denied)
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=stale_allowed,
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-stale-denied",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    repository.get_user.assert_not_called()
    property_repository.get_property.assert_not_called()
    audit_service.append.assert_not_called()


def test_disabled_current_actor_is_denied_before_target_lookup() -> None:
    service, _, repository, _, audit_service, _ = _build_service(
        current_actor=_principal(status="DISABLED")
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-disabled",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    repository.get_user.assert_not_called()
    audit_service.append.assert_not_called()


def test_property_scope_uses_authoritative_tenant_property_lookup() -> None:
    tenant_scope = OrganizationScope(ScopeType.TENANT, tenant_id=11)
    service, _, repository, property_repository, _, _ = _build_service(
        current_actor=_principal(scope=tenant_scope)
    )
    target_scope = OrganizationScope(
        ScopeType.PROPERTY,
        tenant_id=11,
        property_id=22,
    )

    service.grant_user_role(
        actor=_principal(user_id=1),
        target_user_id=20,
        role_code="PROPERTY_ADMIN",
        scope=target_scope,
        request_id="c2-property",
    )

    property_repository.get_property.assert_called_once_with(
        tenant_id=11,
        property_id=22,
    )
    added = repository.add_user_role.call_args.args[0]
    assert (added.tenant_id, added.property_id) == (11, 22)


def test_cross_tenant_actor_is_denied_before_property_lookup() -> None:
    service, _, repository, property_repository, _, _ = _build_service(
        current_actor=_principal(
            scope=OrganizationScope(ScopeType.TENANT, tenant_id=11)
        )
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=12,
                property_id=22,
            ),
            request_id="c2-cross-tenant",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    repository.get_user.assert_not_called()
    property_repository.get_property.assert_not_called()


def test_authoritative_property_mismatch_is_invalid_scope() -> None:
    service, _, repository, property_repository, audit_service, _ = (
        _build_service()
    )
    property_repository.get_property.return_value = None

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=11,
                property_id=22,
            ),
            request_id="c2-property-mismatch",
        )

    assert captured.value.code == IamErrorCode.INVALID_SCOPE
    repository.add_user_role.assert_not_called()
    audit_service.append.assert_not_called()


def test_duplicate_precheck_returns_stable_error() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.find_user_role.return_value = UserRole(id=99)

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-duplicate",
        )

    assert captured.value.code == IamErrorCode.DUPLICATE_GRANT
    repository.add_user_role.assert_not_called()
    audit_service.append.assert_not_called()


def test_only_expected_user_role_unique_1062_maps_to_duplicate() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.add_user_role.side_effect = _integrity_error(
        "Duplicate entry '20-31-1' for key "
        "'user_roles.uq_user_roles_system_grant'"
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-race-duplicate",
        )

    assert captured.value.code == IamErrorCode.DUPLICATE_GRANT
    assert "1062" not in captured.value.message
    audit_service.append.assert_not_called()


def test_unknown_1062_is_not_misreported_as_duplicate_grant() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.add_user_role.side_effect = _integrity_error(
        "Duplicate entry 'x' for key 'some_other_table.uq_other'"
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-unknown-1062",
        )

    assert captured.value.code == IamErrorCode.MUTATION_CONFLICT
    assert "1062" not in captured.value.message
    assert "uq_other" not in captured.value.message
    audit_service.append.assert_not_called()


def test_revoke_uses_locking_lookup_and_preserves_relation_id_in_audit() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.find_user_role_for_update.return_value = UserRole(
        id=303,
        user_id=20,
        role_id=31,
        scope_type="SYSTEM",
    )

    result = service.revoke_user_role(
        actor=_principal(),
        target_user_id=20,
        role_code="PROPERTY_ADMIN",
        scope=OrganizationScope(ScopeType.SYSTEM),
        request_id="c2-revoke",
    )

    repository.delete_user_role.assert_called_once()
    assert result.relation_id == 303
    assert result.action is AuditAction.IAM_USER_ROLE_REVOKED
    audit = audit_service.append.call_args.kwargs
    assert audit["resource_id"] == 303
    assert audit["action"] is AuditAction.IAM_USER_ROLE_REVOKED


def test_revoke_not_found_returns_stable_error_without_audit() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.find_user_role_for_update.return_value = None

    with pytest.raises(AppError) as captured:
        service.revoke_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-revoke-missing",
        )

    assert captured.value.code == IamErrorCode.GRANT_NOT_FOUND
    repository.delete_user_role.assert_not_called()
    audit_service.append.assert_not_called()


def test_role_permission_mutation_is_system_only_and_uses_real_ids() -> None:
    service, _, repository, _, audit_service, _ = _build_service()

    result = service.grant_role_permission(
        actor=_principal(),
        role_code="PROPERTY_ADMIN",
        permission_code="property:read",
        request_id="c2-role-permission",
    )

    relation = repository.add_role_permission.call_args.args[0]
    assert (relation.role_id, relation.permission_id) == (31, 47)
    assert result.relation_id == 202
    audit = audit_service.append.call_args.kwargs
    assert audit["scope_type"] is ScopeType.SYSTEM
    assert audit["tenant_id"] is None
    assert audit["property_id"] is None
    assert audit["details"] == {
        "role_code": "PROPERTY_ADMIN",
        "permission_code": "property:read",
        "source": "INTERNAL",
    }

    tenant_service, _, tenant_repository, _, tenant_audit, _ = _build_service(
        current_actor=_principal(
            scope=OrganizationScope(ScopeType.TENANT, tenant_id=11)
        )
    )
    with pytest.raises(AppError) as captured:
        tenant_service.grant_role_permission(
            actor=_principal(),
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c2-role-permission-denied",
        )
    assert captured.value.code == IamErrorCode.FORBIDDEN
    tenant_repository.get_role_by_code.assert_not_called()
    tenant_audit.append.assert_not_called()


def test_role_permission_duplicate_constraint_is_checked_exactly() -> None:
    service, _, repository, _, _, _ = _build_service()
    repository.add_role_permission.side_effect = _integrity_error(
        "Duplicate entry '31-47' for key "
        "'role_permissions.uq_role_permissions_role_permission'"
    )

    with pytest.raises(AppError) as captured:
        service.grant_role_permission(
            actor=_principal(),
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c2-role-permission-duplicate",
        )

    assert captured.value.code == IamErrorCode.DUPLICATE_GRANT


def test_role_permission_revoke_uses_lock_and_reports_missing() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    repository.find_role_permission_for_update.return_value = RolePermission(
        id=404,
        role_id=31,
        permission_id=47,
    )

    result = service.revoke_role_permission(
        actor=_principal(),
        role_code="PROPERTY_ADMIN",
        permission_code="property:read",
        request_id="c2-role-permission-revoke",
    )

    assert result.relation_id == 404
    repository.delete_role_permission.assert_called_once()
    assert (
        audit_service.append.call_args.kwargs["action"]
        is AuditAction.IAM_ROLE_PERMISSION_REVOKED
    )

    missing_service, _, missing_repository, _, missing_audit, _ = (
        _build_service()
    )
    missing_repository.find_role_permission_for_update.return_value = None
    with pytest.raises(AppError) as captured:
        missing_service.revoke_role_permission(
            actor=_principal(),
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c2-role-permission-missing",
        )
    assert captured.value.code == IamErrorCode.GRANT_NOT_FOUND
    missing_audit.append.assert_not_called()


def test_all_internal_helpers_require_existing_transaction() -> None:
    service, session, _, _, _, _ = _build_service()
    session.in_transaction.return_value = False

    operations = (
        lambda: service._grant_user_role_in_current_transaction(
            actor_user_id=1,
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-no-transaction",
        ),
        lambda: service._revoke_user_role_in_current_transaction(
            actor_user_id=1,
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-no-transaction",
        ),
        lambda: service._grant_role_permission_in_current_transaction(
            actor_user_id=1,
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c2-no-transaction",
        ),
        lambda: service._revoke_role_permission_in_current_transaction(
            actor_user_id=1,
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c2-no-transaction",
        ),
        lambda: service.grant_user_role_for_development_seed(
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c4-no-transaction",
        ),
        lambda: service.grant_role_permission_for_development_seed(
            role_code="PROPERTY_ADMIN",
            permission_code="property:read",
            request_id="c4-no-transaction",
        ),
    )
    for operation in operations:
        with pytest.raises(RuntimeError, match="active transaction"):
            operation()

    session.begin.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_audit_failure_propagates_before_success_return() -> None:
    service, _, repository, _, audit_service, _ = _build_service()
    audit_service.append.side_effect = RuntimeError("forced audit failure")

    with pytest.raises(RuntimeError, match="forced audit failure"):
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-audit-failure",
        )

    repository.add_user_role.assert_called_once()


def test_final_transaction_commit_failure_prevents_success_return() -> None:
    service, session, repository, _, audit_service, _ = _build_service()
    session.in_transaction.return_value = False

    class BoomTxn:
        def __enter__(self) -> object:
            session.in_transaction.return_value = True
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            raise RuntimeError("forced commit failure")

    session.begin.side_effect = BoomTxn

    with pytest.raises(RuntimeError, match="forced commit failure"):
        service.grant_user_role(
            actor=_principal(),
            target_user_id=20,
            role_code="PROPERTY_ADMIN",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-commit-failure",
        )

    repository.add_user_role.assert_called_once()
    audit_service.append.assert_called_once()


def test_development_seed_grant_uses_fixed_source_without_transaction_control() -> None:
    service, session, _, _, audit_service, _ = _build_service()
    session.in_transaction.return_value = True
    session.info[OWNED_TXN_KEY] = 1

    result = service.grant_user_role_for_development_seed(
        target_user_id=20,
        role_code="PROPERTY_ADMIN",
        scope=OrganizationScope(ScopeType.SYSTEM),
        request_id="c4-seed-grant",
    )

    assert result.action is AuditAction.IAM_USER_ROLE_GRANTED
    audit = audit_service.append.call_args.kwargs
    assert audit["actor_user_id"] is None
    assert audit["details"]["source"] == AuditSource.DEVELOPMENT_SEED.value
    session.begin.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_development_seed_role_permission_uses_fixed_source() -> None:
    service, session, _, _, audit_service, _ = _build_service()
    session.in_transaction.return_value = True
    session.info[OWNED_TXN_KEY] = 1

    service.grant_role_permission_for_development_seed(
        role_code="PROPERTY_ADMIN",
        permission_code="property:read",
        request_id="c4-seed-permission",
    )

    audit = audit_service.append.call_args.kwargs
    assert audit["actor_user_id"] is None
    assert audit["details"]["source"] == AuditSource.DEVELOPMENT_SEED.value
    session.begin.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
