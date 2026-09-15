"""Auth Service user validation and Principal construction tests."""

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode
from app.domain.iam import ScopeType
from app.model.identity import User
from app.repository.identity import GrantPermissionRecord, IdentityRepository
from app.service.auth import AuthService


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key="gate-b-auth-service-secret",
    )


def _service() -> tuple[AuthService, MagicMock, MagicMock]:
    session = MagicMock()
    repository = MagicMock(spec=IdentityRepository)
    return AuthService(session, repository), session, repository


def test_development_token_requires_real_active_user() -> None:
    service, session, repository = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="Active User",
    )

    result = service.issue_development_token(7, _settings())

    assert result.token_type == "bearer"
    assert result.expires_in == 900
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_nonexistent_development_user_is_rejected() -> None:
    service, _, repository = _service()
    repository.get_user.return_value = None

    with pytest.raises(AppError) as captured:
        service.issue_development_token(999, _settings())

    assert captured.value.code == AuthErrorCode.INVALID_USER
    assert captured.value.status_code == 401


def test_disabled_development_user_is_rejected() -> None:
    service, _, repository = _service()
    repository.get_user.return_value = User(
        id=7,
        status="DISABLED",
        display_name="Disabled User",
    )

    with pytest.raises(AppError) as captured:
        service.issue_development_token(7, _settings())

    assert captured.value.code == AuthErrorCode.USER_DISABLED


def test_principal_groups_permissions_by_role_grant() -> None:
    service, _, repository = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="Manager",
    )
    repository.list_grant_permissions.return_value = (
        GrantPermissionRecord(
            grant_id=10,
            role_code="PROPERTY_ADMIN",
            scope_type="PROPERTY",
            tenant_id=1,
            property_id=11,
            permission_code="property:read",
        ),
        GrantPermissionRecord(
            grant_id=10,
            role_code="PROPERTY_ADMIN",
            scope_type="PROPERTY",
            tenant_id=1,
            property_id=11,
            permission_code="property:manage",
        ),
        GrantPermissionRecord(
            grant_id=20,
            role_code="RESIDENT",
            scope_type="PROPERTY",
            tenant_id=1,
            property_id=11,
            permission_code=None,
        ),
    )

    principal = service.load_principal(7)

    assert principal.user_id == 7
    assert principal.roles == {"PROPERTY_ADMIN", "RESIDENT"}
    assert principal.permissions == {"property:read", "property:manage"}
    assert len(principal.grants) == 2
    assert principal.grants[0].scope.scope_type is ScopeType.PROPERTY


def test_token_authentication_reloads_user_status() -> None:
    service, _, repository = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="User",
    )
    repository.list_grant_permissions.return_value = ()
    token = service.issue_development_token(7, _settings()).access_token
    repository.get_user.return_value = User(
        id=7,
        status="DISABLED",
        display_name="User",
    )

    with pytest.raises(AppError) as captured:
        service.authenticate_access_token(token, _settings())

    assert captured.value.code == AuthErrorCode.USER_DISABLED
