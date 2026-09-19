"""Official credential security tests that do not touch MySQL or Redis."""

from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest

from app.api.v1.admin_employee import _reject_direct_user_id
from app.core.config import Settings
from app.core.errors import AppError, EmployeeErrorCode
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
    reset_pos = source.index("def reset_password")
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
    assert "/auth/wechat/bind" in source
    assert "latestToken" in source
