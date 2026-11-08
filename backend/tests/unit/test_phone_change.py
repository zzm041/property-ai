"""Logged-in phone change security and page wiring coverage."""

from datetime import UTC, datetime
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from app.core.config import Settings
from app.core.errors import AppError, AuditErrorCode, AuthErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.service.credential_auth import CredentialAuthService
from app.service.audit import AuditService
from app.repository.credential import CredentialRepository


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
        app_secret_key="phone-change-unit-secret",
    )
    service._repository = MagicMock()
    service._repository.get_user_for_update.return_value = SimpleNamespace(
        id=17,
        status="ACTIVE",
        display_name="resident",
    )
    service._audit = MagicMock()
    service._clock = lambda: datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    service._apply_otp = MagicMock(return_value="ok")
    service._revoke_sessions = MagicMock()
    service._issue_session = MagicMock(return_value=SimpleNamespace(session_id=91))
    return service


def test_change_phone_verifies_both_numbers_rebinds_and_rotates_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    old = SimpleNamespace(phone_e164="+8613800001001", status="ACTIVE", ended_at=None)
    user = SimpleNamespace(id=17, status="ACTIVE", display_name="138****1001")
    service._repository.get_active_phone_for_user_for_update.return_value = old
    service._repository.get_active_phone_for_update.return_value = None
    service._repository.get_user_for_update.return_value = user

    issued = service.change_phone(
        user_id=17,
        old_phone="13800001001",
        old_code="111111",
        new_phone="13800001002",
        new_code="222222",
        request_id="phone-change-success",
    )

    assert issued.session_id == 91
    assert old.status == "ENDED"
    assert old.ended_at == datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    assert user.display_name == "138****1002"
    service._apply_otp.assert_has_calls(
        [
            call("+8613800001001", purpose="change_phone", code="111111"),
            call("+8613800001002", purpose="change_phone", code="222222"),
        ]
    )
    added = service._repository.add_phone.call_args.args[0]
    assert added.user_id == 17
    assert added.phone_e164 == "+8613800001002"
    assert added.status == "ACTIVE"
    service._revoke_sessions.assert_called_once_with(
        17,
        reason="PHONE_CHANGE",
        request_id="phone-change-success",
    )
    service._issue_session.assert_called_once_with(user_id=17, auth_method="sms")
    audit = service._audit.append.call_args.kwargs
    assert audit["action"] is AuditAction.AUTH_PHONE_CHANGED
    assert audit["details"]["old_phone"] == "138****1001"
    assert audit["details"]["new_phone"] == "138****1002"


def test_change_phone_rejects_wrong_current_phone_before_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    service._repository.get_active_phone_for_user_for_update.return_value = (
        SimpleNamespace(phone_e164="+8613800001999")
    )

    with pytest.raises(AppError) as captured:
        service.change_phone(
            user_id=17,
            old_phone="13800001001",
            old_code="111111",
            new_phone="13800001002",
            new_code="222222",
            request_id="phone-change-wrong-old",
        )

    assert captured.value.code == AuthErrorCode.OLD_PHONE_MISMATCH
    service._apply_otp.assert_not_called()


def test_change_phone_rejects_registered_target_and_requires_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    service._repository.get_active_phone_for_user_for_update.return_value = (
        SimpleNamespace(phone_e164="+8613800001001")
    )
    service._repository.get_active_phone_for_update.return_value = SimpleNamespace(
        user_id=23
    )

    with pytest.raises(AppError) as captured:
        service.change_phone(
            user_id=17,
            old_phone="13800001001",
            old_code="111111",
            new_phone="13800001002",
            new_code="222222",
            request_id="phone-change-duplicate",
        )

    assert captured.value.code == AuthErrorCode.PHONE_ALREADY_REGISTERED
    assert "merge" in captured.value.message.lower()
    assert service._apply_otp.call_count == 2


def test_change_phone_rejects_invalid_new_code_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    old = SimpleNamespace(phone_e164="+8613800001001", status="ACTIVE", ended_at=None)
    service._repository.get_active_phone_for_user_for_update.return_value = old
    service._apply_otp.side_effect = ["ok", "invalid"]

    with pytest.raises(AppError) as captured:
        service.change_phone(
            user_id=17,
            old_phone="13800001001",
            old_code="111111",
            new_phone="13800001002",
            new_code="000000",
            request_id="phone-change-invalid-new-code",
        )

    assert captured.value.code == AuthErrorCode.INVALID_CODE
    assert old.status == "ACTIVE"
    service._repository.add_phone.assert_not_called()
    service._revoke_sessions.assert_not_called()


def test_change_phone_commits_invalid_otp_attempt_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    service._repository.get_active_phone_for_user_for_update.return_value = (
        SimpleNamespace(phone_e164="+8613800001001")
    )
    service._apply_otp.side_effect = ["ok", "invalid"]
    transaction_outcomes: list[str] = []

    def run_transaction(_session: object, operation: object) -> object:
        result = operation()
        transaction_outcomes.append("committed")
        return result

    monkeypatch.setattr(
        "app.service.credential_auth.run_owned_transaction", run_transaction
    )

    with pytest.raises(AppError) as captured:
        service.change_phone(
            user_id=17,
            old_phone="13800001001",
            old_code="111111",
            new_phone="13800001002",
            new_code="000000",
            request_id="phone-change-invalid-attempt-commit",
        )

    assert captured.value.code == AuthErrorCode.INVALID_CODE
    assert transaction_outcomes == ["committed"]
    service._repository.add_phone.assert_not_called()


def test_phone_change_preserves_custom_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    old = SimpleNamespace(phone_e164="+8613800001001", status="ACTIVE", ended_at=None)
    user = SimpleNamespace(id=17, status="ACTIVE", display_name="张三")
    service._repository.get_active_phone_for_user_for_update.return_value = old
    service._repository.get_active_phone_for_update.return_value = None
    service._repository.get_user_for_update.return_value = user

    service.change_phone(
        user_id=17,
        old_phone="13800001001",
        old_code="111111",
        new_phone="13800001002",
        new_code="222222",
        request_id="phone-change-custom-name",
    )

    assert user.display_name == "张三"


def test_change_phone_rejects_same_number_without_consuming_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)

    with pytest.raises(AppError) as captured:
        service.change_phone(
            user_id=17,
            old_phone="13800001001",
            old_code="111111",
            new_phone="13800001001",
            new_code="222222",
            request_id="phone-change-same",
        )

    assert captured.value.code == AuthErrorCode.INVALID_PHONE
    service._apply_otp.assert_not_called()


def test_change_phone_uses_change_phone_otp_with_login_visual_challenge() -> None:
    service_source = (
        REPO_ROOT / "backend/app/service/credential_auth.py"
    ).read_text(encoding="utf-8")
    schema_source = (REPO_ROOT / "backend/app/schema/auth.py").read_text(
        encoding="utf-8"
    )
    assert 'purpose="login" if purpose == "change_phone" else purpose' in service_source
    visual_source = inspect.getsource(CredentialAuthService.issue_visual_challenge)
    send_source = inspect.getsource(CredentialAuthService.send_sms)
    assert 'purpose not in {"login", "reset"}' in visual_source
    assert 'purpose not in {"login", "reset", "change_phone"}' in send_source
    assert 'Literal["login", "reset", "change_phone"]' in schema_source


def test_profile_page_wires_dual_phone_verification_and_session_replacement() -> None:
    source = (REPO_ROOT / "miniprogram/pages/profile/profile.js").read_text(
        encoding="utf-8"
    )
    template = (REPO_ROOT / "miniprogram/pages/profile/profile.wxml").read_text(
        encoding="utf-8"
    )
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    assert '"change_phone"' in source
    assert "api.issueChallenge(phone, \"login\")" in source
    assert "auth.setToken(token.access_token)" in source
    assert "auth.setRefreshToken(token.refresh_token)" in source
    assert "this.data.displayName === maskPhone(oldPhone)" in source
    assert "app.globalData.me.display_name = displayName" in source
    assert "/auth/phone/change" in api
    assert '"/auth/phone/change": true' in api
    for label in (
        "当前手机号",
        "旧号验证码",
        "新手机号",
        "新号验证码",
        "确认更换手机号",
    ):
        assert label in template


def test_phone_change_audit_policy_requires_masked_phone_values() -> None:
    repository = MagicMock()
    repository.append.return_value = SimpleNamespace(id=1)
    service = AuditService(MagicMock(), repository=repository)

    service.append(
        action=AuditAction.AUTH_PHONE_CHANGED,
        resource_type=AuditResourceType.USER,
        actor_user_id=17,
        resource_id=17,
        request_id="phone-change-audit",
        scope_type=ScopeType.SYSTEM,
        details={
            "old_phone": "138****1001",
            "new_phone": "138****1002",
            "reason_code": "SELF_SERVICE_VERIFIED",
        },
    )

    written = repository.append.call_args.args[0]
    assert written.details_json["old_phone"] == "138****1001"

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.AUTH_PHONE_CHANGED,
            resource_type=AuditResourceType.USER,
            actor_user_id=17,
            resource_id=17,
            request_id="phone-change-audit-raw",
            scope_type=ScopeType.SYSTEM,
            details={
                "old_phone": "13800001001",
                "new_phone": "13800001002",
                "reason_code": "SELF_SERVICE_VERIFIED",
            },
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS


def test_session_rotation_uses_user_lock_before_refresh_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    candidate = SimpleNamespace(user_id=17)
    record = SimpleNamespace(
        user_id=17,
        revoked_at=None,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        auth_method="sms",
        replaced_by_id=None,
    )
    service._repository.get_session_by_refresh_hash_unlocked.return_value = candidate
    service._repository.get_session_by_refresh_hash.return_value = record
    service._repository.reset_mock()

    issued = service.refresh(refresh_token="refresh-value", request_id="refresh-race")

    assert issued.session_id == 91
    assert record.revoked_at == datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    assert record.replaced_by_id == 91
    repository_calls = [item[0] for item in service._repository.method_calls]
    assert repository_calls[:3] == [
        "get_session_by_refresh_hash_unlocked",
        "get_user_for_update",
        "get_session_by_refresh_hash",
    ]


def test_revoke_sessions_locks_user_and_session_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    service._revoke_sessions = CredentialAuthService._revoke_sessions.__get__(
        service, CredentialAuthService
    )
    active = SimpleNamespace(id=51, auth_method="sms", revoked_at=None)
    service._repository.list_active_sessions_for_update.return_value = (active,)
    service._repository.reset_mock()

    service._revoke_sessions(17, reason="PHONE_CHANGE", request_id="revoke-locks")

    assert active.revoked_at == datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    repository_calls = [item[0] for item in service._repository.method_calls]
    assert repository_calls[:2] == [
        "get_user_for_update",
        "list_active_sessions_for_update",
    ]


def test_locked_session_rereads_refresh_identity_map_state() -> None:
    session = MagicMock()
    repository = CredentialRepository(session)

    repository.get_session_by_refresh_hash("hashed-refresh")
    refresh_statement = session.scalar.call_args.args[0]
    assert refresh_statement.get_execution_options()["populate_existing"] is True
    assert refresh_statement._for_update_arg is not None

    repository.get_session_for_update(51)
    id_statement = session.scalar.call_args.args[0]
    assert id_statement.get_execution_options()["populate_existing"] is True
    assert id_statement._for_update_arg is not None


def test_logout_uses_user_before_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    candidate = SimpleNamespace(id=51, user_id=17)
    record = SimpleNamespace(
        id=51,
        user_id=17,
        auth_method="sms",
        revoked_at=None,
    )
    service._repository.get_session_by_refresh_hash_unlocked.return_value = candidate
    service._repository.get_session_by_refresh_hash.return_value = record
    service._repository.reset_mock()

    service.logout(
        session_id=None,
        refresh_token="refresh-value",
        request_id="logout-lock-order",
    )

    assert record.revoked_at == datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    repository_calls = [item[0] for item in service._repository.method_calls]
    assert repository_calls[:3] == [
        "get_session_by_refresh_hash_unlocked",
        "get_user_for_update",
        "get_session_by_refresh_hash",
    ]
