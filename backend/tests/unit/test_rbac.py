"""Principal, default-deny RBAC, Scope, and DevAuth guard tests."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError, IamErrorCode
from app.domain.iam import (
    OrganizationScope,
    Principal,
    RoleGrant,
    ScopeType,
)
from app.main import create_app
from app.service.rbac import RbacService


def _principal(*grants: RoleGrant) -> Principal:
    return Principal(
        user_id=1,
        status="ACTIVE",
        display_name="Test User",
        grants=tuple(grants),
    )


def _grant(
    role_code: str,
    scope: OrganizationScope,
    *permissions: str,
) -> RoleGrant:
    return RoleGrant(
        role_code=role_code,
        scope=scope,
        permissions=frozenset(permissions),
    )


def test_scope_type_excludes_self() -> None:
    assert {item.value for item in ScopeType} == {
        "SYSTEM",
        "TENANT",
        "PROPERTY",
    }
    with pytest.raises(ValueError):
        ScopeType("SELF")


@pytest.mark.parametrize(
    ("scope_type", "tenant_id", "property_id"),
    [
        (ScopeType.SYSTEM, 1, None),
        (ScopeType.TENANT, None, None),
        (ScopeType.TENANT, 1, 2),
        (ScopeType.PROPERTY, 1, None),
    ],
)
def test_invalid_scope_shapes_are_rejected(
    scope_type: ScopeType,
    tenant_id: int | None,
    property_id: int | None,
) -> None:
    with pytest.raises(ValueError, match="Invalid organization scope"):
        OrganizationScope(scope_type, tenant_id, property_id)


def test_system_scope_requires_permission_but_can_cross_organization() -> None:
    principal = _principal(
        _grant(
            "SYSTEM_ADMIN",
            OrganizationScope(ScopeType.SYSTEM),
            "property:read",
        )
    )

    assert principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=9, property_id=99),
    )
    assert not principal.has_permission(
        "property:manage",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=9, property_id=99),
    )


def test_tenant_scope_allows_its_properties_but_not_cross_tenant() -> None:
    principal = _principal(
        _grant(
            "TENANT_ADMIN",
            OrganizationScope(ScopeType.TENANT, tenant_id=1),
            "property:read",
        )
    )

    assert principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
    )
    assert not principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=2, property_id=11),
    )


def test_property_scope_allows_only_exact_property() -> None:
    principal = _principal(
        _grant(
            "PROPERTY_ADMIN",
            OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
            "property:read",
        )
    )

    assert principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
    )
    assert not principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=12),
    )
    assert not principal.has_permission(
        "property:read",
        OrganizationScope(ScopeType.TENANT, tenant_id=1),
    )


def test_multiple_roles_form_permission_union_without_losing_scope() -> None:
    principal = _principal(
        _grant(
            "STAFF",
            OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
            "task:read",
        ),
        _grant(
            "PROPERTY_ADMIN",
            OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
            "property:manage",
        ),
    )
    target = OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11)

    assert principal.roles == {"STAFF", "PROPERTY_ADMIN"}
    assert principal.permissions == {"task:read", "property:manage"}
    assert principal.has_permission("task:read", target)
    assert principal.has_permission("property:manage", target)


def test_rbac_service_is_default_deny() -> None:
    with pytest.raises(AppError) as captured:
        RbacService.require_permission(
            _principal(),
            "property:manage",
            OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=11),
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert captured.value.status_code == 403


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_non_development_environments_reject_dev_auth(app_env: str) -> None:
    with pytest.raises(ValidationError, match="Development Auth"):
        Settings(
            _env_file=None,
            app_env=app_env,
            dev_auth_enabled=True,
            app_secret_key="non-default-production-secret",
        )


def test_production_rejects_default_secret() -> None:
    with pytest.raises(ValidationError, match="non-default APP_SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            dev_auth_enabled=False,
            wechat_login_enabled=False,
        )


def test_dev_auth_route_is_conditionally_mounted() -> None:
    disabled = create_app(
        Settings(_env_file=None, app_env="development", dev_auth_enabled=False)
    )
    enabled = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            dev_auth_enabled=True,
            app_secret_key="gate-b-test-secret",
        )
    )

    disabled_paths = {route.path for route in disabled.routes}
    enabled_paths = {route.path for route in enabled.routes}
    assert "/api/v1/auth/dev/token" not in disabled_paths
    assert "/api/v1/auth/dev/token" in enabled_paths
    assert "/api/v1/me" in disabled_paths
    assert "/api/v1/me" in enabled_paths
