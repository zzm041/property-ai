"""Logged-in password change security, concurrency, and page wiring coverage."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode
from app.core.passwords import hash_password, verify_password
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.repository.credential import CredentialRepository
from app.schema.auth import PasswordChangeRequest
from app.service.credential_auth import CredentialAuthService


REPO_ROOT = Path(__file__).resolve().parents[3]


def _service(monkeypatch: pytest.MonkeyPatch) -> CredentialAuthService:
    monkeypatch.setattr(
        "app.service.credential_auth.run_owned_transaction",
        lambda _session, operation: operation(),
    )
    service = CredentialAuthService.__new__(CredentialAuthService)
    service._session = MagicMock()
    service._settings = Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="password-change-unit-secret",
    )
    service._repository = MagicMock()
    service._repository.get_user_for_update.return_value = SimpleNamespace(
        id=17,
        status="ACTIVE",
    )
    service._audit = MagicMock()
    service._clock = lambda: datetime(2026, 9, 21, 3, 0, tzinfo=UTC)
    service._revoke_sessions = MagicMock()
    service._issue_session = MagicMock(return_value=SimpleNamespace(session_id=91))
    return service


def _credential(password: str = "Current9Pass") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=17,
        password_hash=hash_password(password),
        password_algo="argon2id",
    )


def test_change_password_requires_confirmation_in_contract() -> None:
    with pytest.raises(ValidationError):
        PasswordChangeRequest(
            old_password="Current9Pass",
            new_password="Next9Pass",
        )

    parsed = PasswordChangeRequest(
        old_password="Current9Pass",
        new_password="Next9Pass",
        confirm_password="Next9Pass",
    )
    assert parsed.confirm_password == "Next9Pass"


def test_change_password_rotates_all_sessions_and_preserves_password_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    credential = _credential()
    service._repository.get_password_for_update.return_value = credential

    issued = service.change_password(
        user_id=17,
        old_password="Current9Pass",
        new_password="Next9Pass",
        confirm_password="Next9Pass",
        request_id="password-change-success",
    )

    assert issued.session_id == 91
    assert verify_password(credential.password_hash, "Next9Pass")
    assert not verify_password(credential.password_hash, "next9pass")
    service._revoke_sessions.assert_called_once_with(
        17,
        reason="PASSWORD_CHANGE",
        request_id="password-change-success",
    )
    service._issue_session.assert_called_once_with(user_id=17, auth_method="password")
    audit = service._audit.append.call_args.kwargs
    assert audit["action"] is AuditAction.AUTH_PASSWORD_CHANGED
    assert audit["resource_type"] is AuditResourceType.USER
    assert audit["scope_type"] is ScopeType.SYSTEM
    assert audit["details"] == {"auth_method": "password"}
    repository_calls = [item[0] for item in service._repository.method_calls]
    assert repository_calls[:2] == [
        "get_user_for_update",
        "get_password_for_update",
    ]


def test_change_password_rejects_wrong_current_password_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    credential = _credential()
    original_hash = credential.password_hash
    service._repository.get_password_for_update.return_value = credential

    with pytest.raises(AppError) as captured:
        service.change_password(
            user_id=17,
            old_password="Wrong9Pass",
            new_password="Next9Pass",
            confirm_password="Next9Pass",
            request_id="password-change-wrong-current",
        )

    assert captured.value.code == AuthErrorCode.INVALID_PASSWORD
    assert credential.password_hash == original_hash
    service._revoke_sessions.assert_not_called()
    service._issue_session.assert_not_called()


@pytest.mark.parametrize(
    ("new_password", "confirm_password", "expected_code"),
    [
        ("Next9Pass", "Different9Pass", AuthErrorCode.PASSWORD_MISMATCH),
        ("abcdefgh", "abcdefgh", AuthErrorCode.WEAK_PASSWORD),
        ("12345678", "12345678", AuthErrorCode.WEAK_PASSWORD),
    ],
)
def test_change_password_rejects_mismatch_and_weak_values_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    new_password: str,
    confirm_password: str,
    expected_code: AuthErrorCode,
) -> None:
    service = _service(monkeypatch)
    credential = _credential()
    original_hash = credential.password_hash
    service._repository.get_password_for_update.return_value = credential

    with pytest.raises(AppError) as captured:
        service.change_password(
            user_id=17,
            old_password="Current9Pass",
            new_password=new_password,
            confirm_password=confirm_password,
            request_id="password-change-invalid-new",
        )

    assert captured.value.code == expected_code
    assert credential.password_hash == original_hash
    service._revoke_sessions.assert_not_called()
    service._issue_session.assert_not_called()


def test_password_change_row_lock_refreshes_identity_map_state() -> None:
    session = MagicMock()
    repository = CredentialRepository(session)

    repository.get_password_for_update(17)

    statement = session.scalar.call_args.args[0]
    assert statement.get_execution_options()["populate_existing"] is True
    assert statement._for_update_arg is not None


def test_password_reset_serializes_user_then_password_after_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    ticket = SimpleNamespace(
        user_id=17,
        consumed_at=None,
        expires_at=datetime(2026, 9, 21, 3, 10, tzinfo=UTC),
    )
    credential = _credential()
    service._repository.get_reset_ticket_for_update.return_value = ticket
    service._repository.get_reset_ticket.return_value = ticket
    service._repository.get_password_for_update.return_value = credential
    service._repository.reset_mock()

    service.confirm_password_reset(
        reset_token="valid-reset-token-value",
        new_password="Reset9Pass",
        confirm_password="Reset9Pass",
        request_id="password-reset-lock-order",
    )

    assert ticket.consumed_at == datetime(2026, 9, 21, 3, 0, tzinfo=UTC)
    assert verify_password(credential.password_hash, "Reset9Pass")
    repository_calls = [item[0] for item in service._repository.method_calls]
    assert repository_calls[:4] == [
        "get_reset_ticket",
        "get_user_for_update",
        "get_reset_ticket_for_update",
        "get_password_for_update",
    ]
    service._revoke_sessions.assert_called_once_with(
        17,
        reason="PASSWORD_RESET",
        request_id="password-reset-lock-order",
    )


def test_password_reset_rejects_ticket_consumed_while_waiting_for_user_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    peeked = SimpleNamespace(
        user_id=17,
        consumed_at=None,
        expires_at=datetime(2026, 9, 21, 3, 10, tzinfo=UTC),
    )
    refreshed = SimpleNamespace(
        user_id=17,
        consumed_at=datetime(2026, 9, 21, 3, 0, tzinfo=UTC),
        expires_at=datetime(2026, 9, 21, 3, 10, tzinfo=UTC),
    )
    service._repository.get_reset_ticket.return_value = peeked
    service._repository.get_reset_ticket_for_update.return_value = refreshed

    with pytest.raises(AppError) as captured:
        service.confirm_password_reset(
            reset_token="valid-reset-token-value",
            new_password="Reset9Pass",
            confirm_password="Reset9Pass",
            request_id="password-reset-stale-ticket",
        )

    assert captured.value.code == AuthErrorCode.RESET_TOKEN_INVALID
    service._repository.get_password_for_update.assert_not_called()
    service._revoke_sessions.assert_not_called()


def test_profile_page_wires_password_policy_confirmation_and_session_replacement() -> None:
    source = (REPO_ROOT / "miniprogram/pages/profile/profile.js").read_text(
        encoding="utf-8"
    )
    template = (REPO_ROOT / "miniprogram/pages/profile/profile.wxml").read_text(
        encoding="utf-8"
    )
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    route = (REPO_ROOT / "backend/app/api/v1/credential_auth.py").read_text(
        encoding="utf-8"
    )

    assert "meetsCurrentPolicy(this.data.newPassword)" in source
    assert "this.data.newPassword !== this.data.confirmPassword" in source
    assert "if (this.data.loading)" in source
    assert "if (!this.data.currentPassword)" in source
    assert "auth.setToken(token.access_token)" in source
    assert "auth.setRefreshToken(token.refresh_token)" in source
    assert '"/auth/password/change": true' in api
    assert "confirm_password: confirmPassword" in api
    assert "confirm_password=body.confirm_password" in route
    for label in (
        "当前密码",
        "新密码",
        "确认新密码",
        "不强制大写",
        "确认修改密码",
    ):
        assert label in template
