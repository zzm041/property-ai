"""Auth Service user validation and Principal construction tests."""

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode
from app.domain.iam import ScopeType
from app.model.identity import User
from app.repository.identity import GrantPermissionRecord, IdentityRepository
from app.service.audit import AuditService
from app.service.auth import AuthService


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key="gate-b-auth-service-secret",
    )


def _service() -> tuple[AuthService, MagicMock, MagicMock, MagicMock]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    repository = MagicMock(spec=IdentityRepository)
    audit_service = MagicMock(spec=AuditService)
    return (
        AuthService(session, repository, audit_service),
        session,
        repository,
        audit_service,
    )


def test_development_token_requires_real_active_user() -> None:
    service, session, repository, audit_service = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="Active User",
    )

    result = service.issue_development_token(
        7,
        _settings(),
        request_id="unit-token-issue",
    )

    assert result.token_type == "bearer"
    assert result.expires_in == 900
    audit = audit_service.append.call_args.kwargs
    assert audit["actor_user_id"] == 7
    assert audit["resource_id"] == 7
    assert audit["request_id"] == "unit-token-issue"
    assert audit["tenant_id"] is None
    assert audit["property_id"] is None
    assert audit["details"] == {
        "auth_method": "development",
        "token_type": "access",
    }
    session.begin.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_nonexistent_development_user_is_rejected() -> None:
    service, _, repository, audit_service = _service()
    repository.get_user.return_value = None

    with pytest.raises(AppError) as captured:
        service.issue_development_token(
            999,
            _settings(),
            request_id="missing-user",
        )

    assert captured.value.code == AuthErrorCode.INVALID_USER
    assert captured.value.status_code == 401
    audit_service.append.assert_not_called()


def test_disabled_development_user_is_rejected() -> None:
    service, _, repository, audit_service = _service()
    repository.get_user.return_value = User(
        id=7,
        status="DISABLED",
        display_name="Disabled User",
    )

    with pytest.raises(AppError) as captured:
        service.issue_development_token(
            7,
            _settings(),
            request_id="disabled-user",
        )

    assert captured.value.code == AuthErrorCode.USER_DISABLED
    audit_service.append.assert_not_called()


def test_principal_groups_permissions_by_role_grant() -> None:
    service, session, repository, _ = _service()
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

    session.rollback.assert_not_called()
    session.commit.assert_not_called()
    assert principal.user_id == 7
    assert principal.roles == {"PROPERTY_ADMIN", "RESIDENT"}
    assert principal.permissions == {"property:read", "property:manage"}
    assert len(principal.grants) == 2
    assert principal.grants[0].scope.scope_type is ScopeType.PROPERTY


def test_token_authentication_reloads_user_status() -> None:
    service, _, repository, _ = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="User",
    )
    repository.list_grant_permissions.return_value = ()
    token = service.issue_development_token(
        7,
        _settings(),
        request_id="reload-status",
    ).access_token
    repository.get_user.return_value = User(
        id=7,
        status="DISABLED",
        display_name="User",
    )

    with pytest.raises(AppError) as captured:
        service.authenticate_access_token(token, _settings())

    assert captured.value.code == AuthErrorCode.USER_DISABLED


def test_audit_failure_prevents_token_result() -> None:
    service, _, repository, audit_service = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="Active User",
    )
    audit_service.append.side_effect = RuntimeError("forced audit failure")

    with pytest.raises(RuntimeError, match="forced audit failure"):
        service.issue_development_token(
            7,
            _settings(),
            request_id="audit-failure",
        )


def test_commit_failure_prevents_token_result() -> None:
    service, session, repository, audit_service = _service()
    repository.get_user.return_value = User(
        id=7,
        status="ACTIVE",
        display_name="Active User",
    )
    session.begin.return_value.__exit__.side_effect = RuntimeError(
        "forced commit failure"
    )

    with pytest.raises(RuntimeError, match="forced commit failure"):
        service.issue_development_token(
            7,
            _settings(),
            request_id="commit-failure",
        )

    audit_service.append.assert_called_once()


def test_development_token_resolves_allowed_seed_key() -> None:
    service, _, repository, audit_service = _service()
    repository.get_user_by_display_name.return_value = User(
        id=4,
        status="ACTIVE",
        display_name="__DEV_RESIDENT__",
    )

    result = service.issue_development_token(
        None,
        _settings(),
        request_id="seed-token",
        seed_key="__DEV_RESIDENT__",
    )

    assert result.token_type == "bearer"
    repository.get_user_by_display_name.assert_called_once_with("__DEV_RESIDENT__")
    repository.get_user.assert_not_called()
    assert audit_service.append.call_args.kwargs["actor_user_id"] == 4


def test_unknown_or_disallowed_seed_key_is_rejected() -> None:
    service, _, repository, audit_service = _service()

    with pytest.raises(AppError) as captured:
        service.issue_development_token(
            None,
            _settings(),
            request_id="bad-seed",
            seed_key="__NOT_A_SEED__",
        )

    assert captured.value.code == AuthErrorCode.INVALID_USER
    repository.get_user_by_display_name.assert_not_called()
    audit_service.append.assert_not_called()
