"""RBAC Mutation authorization and Anti-IDOR checks on real MySQL 8."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, IamErrorCode
from app.domain.iam import OrganizationScope, ScopeType
from app.model.identity import AuditLog, UserRole
from app.service.auth import AuthService
from app.service.rbac_mutation import RbacMutationService
from tests.integration.rbac_mutation_test_data import (
    create_rbac_mutation_graph,
    identity_only_principal,
)


def test_stale_allowed_principal_is_denied_after_database_revoke(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    factory = get_session_factory(integration_settings.test_database_url)
    with factory() as auth_session:
        stale_allowed = AuthService(auth_session).load_principal(
            graph.system_actor_id
        )

    with identity_session.begin():
        actor_grant = identity_session.get(
            UserRole,
            graph.system_actor_grant_id,
        )
        assert actor_grant is not None
        identity_session.delete(actor_grant)

    with pytest.raises(AppError) as captured:
        RbacMutationService(identity_session).grant_user_role(
            actor=stale_allowed,
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-stale-principal",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert identity_session.scalar(
        select(func.count())
        .select_from(UserRole)
        .where(UserRole.user_id == graph.target_user_id)
    ) == 0
    assert identity_session.scalar(
        select(func.count()).select_from(AuditLog)
    ) == 0


def test_disabled_actor_is_denied_using_current_database_status(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)

    with pytest.raises(AppError) as captured:
        RbacMutationService(identity_session).grant_user_role(
            actor=identity_only_principal(graph.disabled_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=OrganizationScope(ScopeType.SYSTEM),
            request_id="c2-disabled-actor",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert identity_session.scalar(
        select(func.count()).select_from(AuditLog)
    ) == 0


@pytest.mark.parametrize(
    "actor_and_scope",
    [
        "tenant_cross_tenant",
        "property_cross_property",
        "property_tenant_scope",
    ],
)
def test_cross_scope_mutation_is_denied(
    identity_session: Session,
    actor_and_scope: str,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    if actor_and_scope == "tenant_cross_tenant":
        actor_id = graph.tenant_actor_id
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
        )
    elif actor_and_scope == "property_cross_property":
        actor_id = graph.property_actor_id
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_2_id,
        )
    else:
        actor_id = graph.property_actor_id
        scope = OrganizationScope(
            ScopeType.TENANT,
            tenant_id=graph.tenant_1_id,
        )

    with pytest.raises(AppError) as captured:
        RbacMutationService(identity_session).grant_user_role(
            actor=identity_only_principal(actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=scope,
            request_id=f"c2-{actor_and_scope}",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert identity_session.scalar(
        select(func.count())
        .select_from(UserRole)
        .where(UserRole.user_id == graph.target_user_id)
    ) == 0


def test_guessed_property_id_cannot_bypass_authoritative_lookup(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)

    with pytest.raises(AppError) as captured:
        RbacMutationService(identity_session).grant_user_role(
            actor=identity_only_principal(graph.tenant_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=graph.tenant_1_id,
                property_id=9_223_372_036_854_775_000,
            ),
            request_id="c2-guessed-property",
        )

    assert captured.value.code == IamErrorCode.INVALID_SCOPE
    assert identity_session.scalar(
        select(func.count())
        .select_from(UserRole)
        .where(UserRole.user_id == graph.target_user_id)
    ) == 0


def test_role_permission_mutation_requires_system_scope(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)

    with pytest.raises(AppError) as captured:
        RbacMutationService(identity_session).grant_role_permission(
            actor=identity_only_principal(graph.tenant_actor_id),
            role_code="STAFF",
            permission_code="property:read",
            request_id="c2-tenant-role-permission",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert identity_session.scalar(
        select(func.count()).select_from(AuditLog)
    ) == 0


@pytest.mark.parametrize(
    ("forged_field", "forged_value"),
    [
        ("actor_user_id", 1),
        ("source", "DEVELOPMENT_SEED"),
        ("details", {"source": "DEVELOPMENT_SEED"}),
    ],
)
def test_public_mutation_does_not_accept_forged_audit_context(
    identity_session: Session,
    forged_field: str,
    forged_value: object,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    values: dict[str, object] = {
        "actor": identity_only_principal(graph.system_actor_id),
        "target_user_id": graph.target_user_id,
        "role_code": "STAFF",
        "scope": OrganizationScope(ScopeType.SYSTEM),
        "request_id": "c2-forged-context",
        forged_field: forged_value,
    }

    with pytest.raises(TypeError):
        RbacMutationService(identity_session).grant_user_role(**values)

    assert identity_session.scalar(
        select(func.count())
        .select_from(UserRole)
        .where(UserRole.user_id == graph.target_user_id)
    ) == 0


def test_duplicate_error_does_not_expose_sql_or_constraint_name(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    service = RbacMutationService(identity_session)
    arguments = {
        "actor": identity_only_principal(graph.system_actor_id),
        "target_user_id": graph.target_user_id,
        "role_code": "STAFF",
        "scope": OrganizationScope(ScopeType.SYSTEM),
    }
    service.grant_user_role(
        **arguments,
        request_id="c2-safe-duplicate-first",
    )

    with pytest.raises(AppError) as captured:
        service.grant_user_role(
            **arguments,
            request_id="c2-safe-duplicate-second",
        )

    assert captured.value.code == IamErrorCode.DUPLICATE_GRANT
    exposed = f"{captured.value.message} {captured.value.details}".casefold()
    assert "1062" not in exposed
    assert "insert into" not in exposed
    assert "uq_user_roles" not in exposed
