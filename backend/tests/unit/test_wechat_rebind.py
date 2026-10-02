"""Verified WeChat rebind service and mini-program wiring coverage."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AppError, AuditErrorCode, AuthErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.provider.wechat import WeChatSession
from app.service.audit import AuditService
from app.service.wechat_auth import WeChatAuthService


REPO_ROOT = Path(__file__).resolve().parents[3]


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        wechat_login_enabled=True,
        wechat_provider="development",
        app_secret_key="wechat-rebind-unit-secret",
    )


def _service(monkeypatch: pytest.MonkeyPatch, candidates: list[object]):
    transaction_count = {"value": 0}

    def run_transaction(_session: object, operation: object) -> object:
        transaction_count["value"] += 1
        return operation()

    monkeypatch.setattr(
        "app.service.wechat_auth.run_owned_transaction", run_transaction
    )
    monkeypatch.setattr(
        "app.service.wechat_auth.build_wechat_auth_provider",
        lambda _settings: SimpleNamespace(
            exchange_code=lambda _code: WeChatSession(
                appid="wx-dev-app",
                openid="openid-new-target",
                unionid=None,
            )
        ),
    )
    credential_repository = MagicMock()
    credential_repository.get_active_phone_for_user_for_update.return_value = (
        SimpleNamespace(phone_e164="+8613800002001")
    )
    monkeypatch.setattr(
        "app.service.wechat_auth.CredentialRepository",
        lambda _session: credential_repository,
    )
    issued = SimpleNamespace(session_id=902)
    credential = SimpleNamespace(
        _require_phone=lambda _phone: "+8613800002001",
        _apply_otp=MagicMock(return_value="ok"),
        _raise_otp=MagicMock(),
        _lock_active_user=MagicMock(
            return_value=SimpleNamespace(id=17, status="ACTIVE")
        ),
        _revoke_sessions=MagicMock(),
        _issue_session=MagicMock(return_value=issued),
    )
    monkeypatch.setattr(
        "app.service.wechat_auth.CredentialAuthService",
        lambda *_args, **_kwargs: credential,
    )
    repository = MagicMock()
    repository.list_user_wechat_rebind_candidates_for_update.return_value = tuple(
        candidates
    )
    identity = MagicMock()
    identity.get_user.return_value = SimpleNamespace(id=17, status="ACTIVE")
    audit = MagicMock()
    session = MagicMock()
    service = WeChatAuthService(
        session,
        repository=repository,
        identity_repository=identity,
        audit_service=audit,
    )
    return (
        service,
        session,
        repository,
        credential_repository,
        credential,
        audit,
        issued,
        transaction_count,
    )


def test_rebind_verifies_phone_replaces_binding_and_rotates_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keeper = SimpleNamespace(
        id=31,
        user_id=17,
        appid="wx-dev-app",
        openid="openid-old-primary",
        unionid=None,
    )
    stale = SimpleNamespace(
        id=32,
        user_id=17,
        appid="wx-dev-app",
        openid="openid-old-stale",
        unionid=None,
    )
    (
        service,
        session,
        repository,
        _credential_repository,
        credential,
        audit,
        issued,
        transaction_count,
    ) = _service(monkeypatch, [keeper, stale])

    result = service.rebind(
        user_id=17,
        code="wx-new-target",
        phone="13800002001",
        sms_code="123456",
        settings=_settings(),
        request_id="wechat-rebind-success",
    )

    assert result is issued
    assert transaction_count["value"] == 2
    credential._apply_otp.assert_called_once_with(
        "+8613800002001", purpose="login", code="123456"
    )
    repository.list_user_wechat_rebind_candidates_for_update.assert_called_once_with(
        user_id=17,
        appid="wx-dev-app",
        openid="openid-new-target",
        unionid=None,
    )
    assert keeper.openid == "openid-new-target"
    assert keeper.unionid is None
    session.delete.assert_called_once_with(stale)
    credential._revoke_sessions.assert_called_once_with(
        17,
        reason="WECHAT_REBIND",
        request_id="wechat-rebind-success",
    )
    credential._issue_session.assert_called_once_with(
        user_id=17,
        auth_method="sms",
    )
    audit_call = audit.append.call_args.kwargs
    assert audit_call["action"] is AuditAction.AUTH_WECHAT_REBOUND
    assert audit_call["resource_id"] == 31
    assert audit_call["details"] == {
        "auth_method": "sms",
        "reason_code": "SELF_SERVICE_VERIFIED",
        "replaced_binding_count": 2,
    }


def test_rebind_commits_invalid_otp_attempt_before_rejecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        service,
        _session,
        repository,
        _credential_repository,
        credential,
        _audit,
        _issued,
        transaction_count,
    ) = _service(monkeypatch, [])
    credential._apply_otp.return_value = "invalid"
    credential._raise_otp.side_effect = AppError(
        code=AuthErrorCode.INVALID_CODE,
        message="Verification code is invalid",
        status_code=401,
    )

    with pytest.raises(AppError) as captured:
        service.rebind(
            user_id=17,
            code="wx-new-target",
            phone="13800002001",
            sms_code="000000",
            settings=_settings(),
            request_id="wechat-rebind-invalid-otp",
        )

    assert captured.value.code == AuthErrorCode.INVALID_CODE
    assert transaction_count["value"] == 1
    repository.list_user_wechat_rebind_candidates_for_update.assert_not_called()
    credential._revoke_sessions.assert_not_called()


def test_rebind_rejects_takeover_without_mutating_existing_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = SimpleNamespace(
        id=31,
        user_id=17,
        appid="wx-dev-app",
        openid="openid-old-primary",
        unionid=None,
    )
    other_user_target = SimpleNamespace(
        id=88,
        user_id=23,
        appid="wx-dev-app",
        openid="openid-new-target",
        unionid=None,
    )
    (
        service,
        session,
        _repository,
        _credential_repository,
        credential,
        audit,
        _issued,
        _transaction_count,
    ) = _service(monkeypatch, [current, other_user_target])

    with pytest.raises(AppError) as captured:
        service.rebind(
            user_id=17,
            code="wx-new-target",
            phone="13800002001",
            sms_code="123456",
            settings=_settings(),
            request_id="wechat-rebind-takeover",
        )

    assert captured.value.code == AuthErrorCode.WECHAT_ALREADY_BOUND
    assert "merge" in captured.value.message.lower()
    assert current.openid == "openid-old-primary"
    session.delete.assert_not_called()
    credential._revoke_sessions.assert_not_called()
    audit.append.assert_not_called()


def test_bind_requires_rebind_when_account_already_has_app_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = SimpleNamespace(
        id=31,
        user_id=17,
        appid="wx-dev-app",
        openid="openid-old-primary",
        unionid=None,
    )
    (
        service,
        _session,
        _repository,
        _credential_repository,
        _credential,
        audit,
        _issued,
        _transaction_count,
    ) = _service(monkeypatch, [current])

    with pytest.raises(AppError) as captured:
        service.bind(
            user_id=17,
            code="wx-new-target",
            settings=_settings(),
            request_id="wechat-bind-must-rebind",
        )

    assert captured.value.code == AuthErrorCode.WECHAT_REBIND_REQUIRED
    audit.append.assert_not_called()


def test_wechat_rebind_audit_policy_rejects_invalid_count() -> None:
    repository = MagicMock()
    repository.append.return_value = SimpleNamespace(id=1)
    service = AuditService(MagicMock(), repository=repository)

    service.append(
        action=AuditAction.AUTH_WECHAT_REBOUND,
        resource_type=AuditResourceType.USER_WECHAT,
        actor_user_id=17,
        resource_id=31,
        request_id="wechat-rebind-audit",
        scope_type=ScopeType.SYSTEM,
        details={
            "auth_method": "sms",
            "reason_code": "SELF_SERVICE_VERIFIED",
            "replaced_binding_count": 1,
        },
    )

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.AUTH_WECHAT_REBOUND,
            resource_type=AuditResourceType.USER_WECHAT,
            actor_user_id=17,
            resource_id=31,
            request_id="wechat-rebind-audit-invalid",
            scope_type=ScopeType.SYSTEM,
            details={
                "auth_method": "sms",
                "reason_code": "SELF_SERVICE_VERIFIED",
                "replaced_binding_count": 0,
            },
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS


def test_miniprogram_wires_verified_wechat_rebind_and_replacement_session() -> None:
    source = (REPO_ROOT / "miniprogram/pages/profile/profile.js").read_text(
        encoding="utf-8"
    )
    template = (REPO_ROOT / "miniprogram/pages/profile/profile.wxml").read_text(
        encoding="utf-8"
    )
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    route = (REPO_ROOT / "backend/app/api/v1/wechat_auth.py").read_text(
        encoding="utf-8"
    )

    assert 'api.sendSms(\n        this.data.wechatPhone,\n        "login"' in source
    assert "wechatRebind" in source
    assert "auth.setToken(token.access_token)" in source
    assert "auth.setRefreshToken(token.refresh_token)" in source
    assert '"/auth/wechat/rebind": true' in api
    assert '@router.post("/rebind")' in route
    assert 'auth_method not in {"sms", "password", "wechat"}' in route
    for label in (
        "安全更换微信",
        "当前账号手机号",
        "当前手机号验证码",
        "确认更换为当前微信",
    ):
        assert label in template


def test_wechat_bind_and_login_buttons_have_stable_page_ids() -> None:
    profile = (REPO_ROOT / "miniprogram/pages/profile/profile.wxml").read_text(
        encoding="utf-8"
    )
    login = (REPO_ROOT / "miniprogram/pages/login/login.wxml").read_text(
        encoding="utf-8"
    )

    assert 'id="wechat-bind-button"' in profile
    assert 'id="wechat-login-button"' in login

    auth = (REPO_ROOT / "miniprogram/services/auth.js").read_text(
        encoding="utf-8"
    )
    assert 'ENV_VERSION === "develop"' in auth
    assert "wx-dev-device-v1:" in auth
    assert "development_wechat_device_id" in auth
