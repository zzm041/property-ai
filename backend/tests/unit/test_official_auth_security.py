"""Official credential security tests that do not touch MySQL or Redis."""

from datetime import UTC, datetime
from pathlib import Path
import subprocess

import jwt
import pytest

from app.api.v1.admin_employee import _reject_direct_user_id
from app.core.config import Settings
from app.core.errors import AppError, EmployeeErrorCode, IamErrorCode
from app.domain.iam import Principal
from app.core.passwords import hash_password, verify_password
from app.core.rate_limit import MemoryRateLimiter, RedisRateLimiter
from app.core.security import (
    InvalidAccessTokenError,
    decode_access_token,
    issue_access_token,
)
from app.core.token_hash import keyed_hash
from app.domain.phone import mask_phone, normalize_phone
from app.provider.sms import DevelopmentSmsProvider


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "dev_auth_enabled": True,
        "app_secret_key": "official-auth-test-secret-not-production",
        "jwt_issuer": "test-issuer",
        "jwt_audience": "test-audience",
    }
    values.update(overrides)
    return Settings(**values)


def test_password_is_argon2id_and_wrong_secret_fails() -> None:
    digest = hash_password("CorrectHorseBattery")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "CorrectHorseBattery")
    assert not verify_password(digest, "wrong-password")


def test_phone_normalization_and_masking() -> None:
    assert normalize_phone("13800138000") == "+8613800138000"
    assert normalize_phone("+86 138-0013-8000") == "+8613800138000"
    assert normalize_phone("abc") is None
    assert mask_phone("+8613800138000") == "138****8000"


def test_otp_hash_is_one_way() -> None:
    secret = "official-auth-test-secret-not-production"
    digest = keyed_hash("123456", secret=secret)
    assert digest != "123456"
    assert keyed_hash("123456", secret=secret) == digest
    assert keyed_hash("000000", secret=secret) != digest


def test_sms_and_password_tokens_require_session_and_are_not_dev() -> None:
    settings = _settings(dev_auth_enabled=False)
    sms = issue_access_token(9, settings, auth_method="sms", session_id="12")
    password = issue_access_token(9, settings, auth_method="password", session_id="12")
    for token in (sms, password):
        payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
        assert payload["sid"] == "12"
        assert payload["auth_method"] in {"sms", "password"}
        assert "roles" not in payload
        claims = decode_access_token(token, settings)
        assert claims.session_id == "12"
        assert claims.user_id == 9
    with pytest.raises(ValueError):
        issue_access_token(9, settings, auth_method="sms")


def test_legacy_wechat_token_without_session_still_decodes() -> None:
    settings = _settings(dev_auth_enabled=False)
    token = issue_access_token(9, settings, auth_method="wechat")
    claims = decode_access_token(token, settings)
    assert claims.auth_method == "wechat"
    assert claims.session_id is None


def test_sms_token_without_sid_is_rejected() -> None:
    settings = _settings()
    payload = jwt.decode(
        issue_access_token(9, settings, auth_method="wechat"),
        options={"verify_signature": False},
        algorithms=["HS256"],
    )
    payload["auth_method"] = "sms"
    token = jwt.encode(
        payload,
        settings.app_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings)


def test_memory_rate_limiter_blocks_after_limit() -> None:
    limiter = MemoryRateLimiter(clock=lambda: datetime(2026, 9, 19, tzinfo=UTC))
    assert limiter.hit("otp:1", limit=2, window_seconds=60)
    assert limiter.hit("otp:1", limit=2, window_seconds=60)
    assert not limiter.hit("otp:1", limit=2, window_seconds=60)


def test_development_sms_is_labeled_and_capturable() -> None:
    provider = DevelopmentSmsProvider()
    result = provider.send(phone_e164="+8613800138000", purpose="login", code="654321")
    assert result.delivery_channel == "development"
    assert result.provider_mode == "DEVELOPMENT"
    assert provider.last_code("+8613800138000", "login") == "654321"


def test_redis_limiter_script_is_atomic_incr_and_expire() -> None:
    script = RedisRateLimiter._INCR_EXPIRE
    assert "redis.call('INCR'" in script
    assert "redis.call('EXPIRE'" in script
    assert script.index("INCR") < script.index("EXPIRE")


def test_direct_employee_user_id_rejected_outside_dev_test() -> None:
    staging = _settings(app_env="staging", dev_auth_enabled=False)
    with pytest.raises(AppError) as error:
        _reject_direct_user_id(user_id=1, settings=staging)
    assert error.value.code == EmployeeErrorCode.INVALID_INPUT
    _reject_direct_user_id(user_id=None, settings=staging)
    _reject_direct_user_id(user_id=1, settings=_settings())


def test_wechat_bind_requires_sms_or_password_session() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "wechat_auth.py"
    ).read_text(encoding="utf-8")
    assert 'auth_method not in {"sms", "password"}' in source
    assert "IAM.FORBIDDEN" in source or "IamErrorCode.FORBIDDEN" in source


def test_wechat_login_issues_refresh_session() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "service" / "wechat_auth.py"
    ).read_text(encoding="utf-8")
    assert "_issue_session" in source
    assert 'auth_method="wechat"' in source


def test_wechat_token_with_session_decodes() -> None:
    settings = _settings(dev_auth_enabled=False)
    token = issue_access_token(9, settings, auth_method="wechat", session_id="88")
    claims = decode_access_token(token, settings)
    assert claims.session_id == "88"
    assert claims.auth_method == "wechat"


def test_otp_failure_is_raised_after_owned_transaction() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "service" / "credential_auth.py"
    ).read_text(encoding="utf-8")
    assert "self._raise_otp(otp_status[\"value\"])" in source
    login_pos = source.index("def login_sms")
    reset_pos = source.index("def verify_password_reset")
    raise_login = source.index("self._raise_otp(otp_status[\"value\"])", login_pos)
    raise_reset = source.index("self._raise_otp(otp_status[\"value\"])", reset_pos)
    assert source.index("run_owned_transaction(self._session, _persist)", login_pos) < raise_login
    assert source.index("run_owned_transaction(self._session, _persist)", reset_pos) < raise_reset


def test_employee_claim_locks_employee_row() -> None:
    service = (
        Path(__file__).resolve().parents[2] / "app" / "service" / "employee_identity.py"
    ).read_text(encoding="utf-8")
    repository = (
        Path(__file__).resolve().parents[2] / "app" / "repository" / "employee.py"
    ).read_text(encoding="utf-8")
    assert "get_employee_for_update" in repository
    assert "with_for_update()" in repository
    assert service.count("get_employee_for_update") >= 2
    assert service.count("with_for_update()") >= 2
    claim_pos = service.index("def claim")
    issue_pos = service.index("def issue_invite")
    assert service.index("get_employee_for_update", issue_pos) < service.index(
        "with_for_update()", issue_pos
    )
    assert service.index("get_employee_for_update", claim_pos) < service.index(
        "with_for_update()", claim_pos
    )
    invalid_invite = 'message="Invitation is invalid or expired"'
    assert service.count(invalid_invite) == 3
    for index in range(3):
        pos = -1
        for _ in range(index + 1):
            pos = service.index(invalid_invite, pos + 1)
        window = service[pos : pos + 120]
        assert "status_code=400" in window
        assert "status_code=401" not in window


class _InviteQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    def filter(self, *args: object, **kwargs: object) -> "_InviteQuery":
        return self

    def with_for_update(self) -> "_InviteQuery":
        return self

    def one_or_none(self) -> object:
        return self._result


def _claim_actor() -> Principal:
    return Principal(user_id=21, status="ACTIVE", display_name="staff2", grants=())


def test_claim_unknown_or_consumed_invite_is_400_not_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.service.employee_identity import EmployeeIdentityService

    monkeypatch.setattr(
        "app.service.employee_identity.run_owned_transaction",
        lambda session, fn: fn(),
    )
    session = MagicMock()
    session.query.return_value = _InviteQuery(None)
    service = EmployeeIdentityService(session, secret="test-secret")
    with pytest.raises(AppError) as unknown:
        service.claim(actor=_claim_actor(), token="not-a-real-token", request_id="r1")
    assert unknown.value.status_code == 400
    assert unknown.value.code == EmployeeErrorCode.INVALID_INPUT
    session.commit.assert_not_called()
    session.rollback.assert_not_called()

    peeked = SimpleNamespace(employee_id=4)
    consumed = SimpleNamespace(
        employee_id=4,
        status="CONSUMED",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        target_phone_e164="+8613900002881",
    )
    employee = SimpleNamespace(id=4, status="ACTIVE", user_id=None)
    session.query.side_effect = [_InviteQuery(peeked), _InviteQuery(consumed)]
    service._employees = MagicMock()
    service._employees.get_employee_for_update.return_value = employee
    with pytest.raises(AppError) as reused:
        service.claim(actor=_claim_actor(), token="consumed-token", request_id="r2")
    assert reused.value.status_code == 400
    assert reused.value.code == EmployeeErrorCode.INVALID_INPUT
    session.commit.assert_not_called()


def test_claim_wrong_phone_stays_403(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.service.employee_identity import EmployeeIdentityService

    monkeypatch.setattr(
        "app.service.employee_identity.run_owned_transaction",
        lambda session, fn: fn(),
    )
    peeked = SimpleNamespace(employee_id=4)
    active = SimpleNamespace(
        employee_id=4,
        status="ACTIVE",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        target_phone_e164="+8613900000001",
    )
    session = MagicMock()
    session.query.side_effect = [_InviteQuery(peeked), _InviteQuery(active)]
    service = EmployeeIdentityService(session, secret="test-secret")
    service._employees = MagicMock()
    service._employees.get_employee_for_update.return_value = SimpleNamespace(
        id=4, status="ACTIVE", user_id=None
    )
    service._credentials = MagicMock()
    service._credentials.get_active_phone_for_user.return_value = SimpleNamespace(
        phone_e164="+8613900002881"
    )
    with pytest.raises(AppError) as denied:
        service.claim(actor=_claim_actor(), token="other-phone-token", request_id="r3")
    assert denied.value.status_code == 403
    assert denied.value.code == IamErrorCode.FORBIDDEN
    session.commit.assert_not_called()


def test_miniprogram_refresh_retries_once() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "miniprogram"
        / "services"
        / "api.js"
    ).read_text(encoding="utf-8")
    assert "refreshSessionOnce" in source
    assert "retried: true" in source
    assert "CREDENTIAL_AUTH_PATHS" in source
    assert "refreshInFlight" in source
    assert "latestToken" in source
    assert "/auth/wechat/bind" in source
    assert "/auth/challenge" in source
    assert "/auth/password/reset/verify" in source
    assert "/auth/password/reset/confirm" in source
    assert "/auth/sms/test-inbox" in source
    login_page = (
        Path(__file__).resolve().parents[3] / "miniprogram" / "pages" / "login" / "login.wxml"
    ).read_text(encoding="utf-8")
    login_js = (
        Path(__file__).resolve().parents[3] / "miniprogram" / "pages" / "login" / "login.js"
    ).read_text(encoding="utf-8")
    assert "以住户登录" not in login_page
    assert "以物业登录" not in login_page
    assert "以员工登录" not in login_page
    assert "dev-login" not in login_js
    assert "/auth/dev/token" not in login_js
    assert "mode === 'password'" in login_page
    assert "mode === 'sms'" in login_page
    assert "忘记密码" in login_page
    reset_js = (
        Path(__file__).resolve().parents[3]
        / "miniprogram"
        / "pages"
        / "password-reset"
        / "password-reset.js"
    ).read_text(encoding="utf-8")
    assert "confirmPasswordReset" in reset_js
    assert "redirectTo" in reset_js


def test_new_password_policy_rejects_weak_and_login_does_not_use_it() -> None:
    from app.core.passwords import password_strength, require_new_password

    require_new_password("GoodPass1")
    require_new_password("Abcd1234")
    require_new_password("goodpass1")
    require_new_password("goodpass!")
    require_new_password("一二三四五六七八")
    require_new_password("😀😀😀😀😀😀😀😀")
    require_new_password("１２３４５６７８")
    with pytest.raises(AppError) as digits:
        require_new_password("12345678")
    assert digits.value.code == "AUTH.WEAK_PASSWORD"
    with pytest.raises(AppError):
        require_new_password("abcdefgh")
    with pytest.raises(AppError):
        require_new_password("short")
    with pytest.raises(AppError):
        require_new_password("😀😀😀😀")
    assert password_strength("12345678") == "偏弱"
    assert password_strength("abcdefgh") == "偏弱"
    assert password_strength("😀😀😀😀") == "偏弱"
    assert password_strength("goodpass1") == "可用"
    assert password_strength("GoodPass1") in {"可用", "较强"}
    assert password_strength("CorrectHorse1!") == "较强"
    digest = hash_password("goodpass1")
    assert digest.startswith("$argon2id$")
    assert verify_password(digest, "goodpass1")
    assert not verify_password(digest, "GOODPASS1")
    assert not verify_password(digest, "Goodpass1")

    source = (
        Path(__file__).resolve().parents[2] / "app" / "service" / "credential_auth.py"
    ).read_text(encoding="utf-8")
    login_pos = source.index("def login_password")
    change_pos = source.index("def change_password")
    login_body = source[login_pos:change_pos]
    assert "require_new_password" not in login_body
    assert "verify_password" in login_body
    assert "require_new_password" in source[change_pos:]
    assert "def confirm_password_reset" in source
    confirm_pos = source.index("def confirm_password_reset")
    assert "require_new_password" in source[confirm_pos : source.index("def refresh")]
    hasher = (
        Path(__file__).resolve().parents[2] / "app" / "core" / "passwords.py"
    ).read_text(encoding="utf-8")
    hash_pos = hasher.index("def hash_password")
    verify_pos = hasher.index("def verify_password")
    strength_pos = hasher.index("def password_strength")
    hash_body = hasher[hash_pos:verify_pos]
    verify_body = hasher[verify_pos:strength_pos]
    assert "casefold(" not in hash_body
    assert "password.lower(" not in hash_body
    assert "casefold(" not in verify_body
    assert "password.lower(" not in verify_body
    frontend = (
        Path(__file__).resolve().parents[3] / "miniprogram" / "utils" / "password.js"
    ).read_text(encoding="utf-8")
    assert "toLowerCase" not in frontend
    assert "toUpperCase" not in frontend
    reset_page = (
        Path(__file__).resolve().parents[3]
        / "miniprogram"
        / "pages"
        / "password-reset"
        / "password-reset.wxml"
    ).read_text(encoding="utf-8")
    assert "不强制大写" in reset_page
    frontend_check = (
        "const p=require('./miniprogram/utils/password');"
        "if(!p.meetsCurrentPolicy('goodpass1')) process.exit(2);"
        "if(p.strength('goodpass1')!=='可用') process.exit(3);"
        "if(p.meetsCurrentPolicy('abcdefgh')) process.exit(4);"
        "if(p.strength('abcdefgh')!=='偏弱') process.exit(5);"
        "if(p.meetsCurrentPolicy('😀😀😀😀')) process.exit(6);"
        "if(p.strength('😀😀😀😀')!=='偏弱') process.exit(7);"
        "if(!p.meetsCurrentPolicy('😀😀😀😀😀😀😀😀')) process.exit(8);"
        "if(!p.meetsCurrentPolicy('一二三四五六七八')) process.exit(9);"
        "if(!p.meetsCurrentPolicy('１２３４５６７８')) process.exit(10);"
        "if(p.codePoints('😀😀😀😀').length!==4) process.exit(11);"
    )
    completed = subprocess.run(
        ["node", "-e", frontend_check],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_isolated_auth_guard_rejects_shared_schemas_and_port_only() -> None:
    from app.core.database import assert_isolated_auth_database

    shared = (
        "mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/"
        "property_ai_test?charset=utf8mb4"
    )
    day4 = (
        "mysql+pymysql://property:change-me@127.0.0.1:3306/"
        "property_ai_day4?charset=utf8mb4"
    )
    localhost = (
        "mysql+pymysql://property_test:change-me-test@localhost:3307/"
        "property_ai_auth001_test?charset=utf8mb4"
    )
    exact = (
        "mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/"
        "property_ai_auth001_test?charset=utf8mb4"
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=shared,
        database_url=(
            "mysql+pymysql://property:change-me@127.0.0.1:3306/"
            "property_ai?charset=utf8mb4"
        ),
    )
    with pytest.raises(RuntimeError, match="protected database"):
        assert_isolated_auth_database(settings, database_url=shared)
    with pytest.raises(RuntimeError, match="protected database"):
        assert_isolated_auth_database(settings, database_url=day4)
    with pytest.raises(RuntimeError, match="must target"):
        assert_isolated_auth_database(settings, database_url=localhost)
    development = Settings(
        _env_file=None,
        app_env="development",
        allow_destructive_test_database=True,
        test_database_url=exact,
        database_url=(
            "mysql+pymysql://property:change-me@127.0.0.1:3306/"
            "property_ai?charset=utf8mb4"
        ),
    )
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        assert_isolated_auth_database(development, database_url=exact)
    denied_flag = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=False,
        test_database_url=exact,
        database_url=(
            "mysql+pymysql://property:change-me@127.0.0.1:3306/"
            "property_ai?charset=utf8mb4"
        ),
    )
    with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE"):
        assert_isolated_auth_database(denied_flag, database_url=exact)
    allowed = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=exact,
        database_url=(
            "mysql+pymysql://property:change-me@127.0.0.1:3306/"
            "property_ai?charset=utf8mb4"
        ),
    )
    assert_isolated_auth_database(allowed, database_url=exact)


def test_send_sms_requires_server_challenge_and_does_not_return_code() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "service" / "credential_auth.py"
    ).read_text(encoding="utf-8")
    send_pos = source.index("def send_sms")
    login_pos = source.index("def login_sms")
    body = source[send_pos:login_pos]
    assert "_consume_visual_challenge" in body
    assert "SmsSendData" in body
    assert "code=" not in body.split("return SmsSendData")[1][:400]
    api = (
        Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "credential_auth.py"
    ).read_text(encoding="utf-8")
    assert "test-inbox" in api
    assert 'settings.app_env != "test"' in api
