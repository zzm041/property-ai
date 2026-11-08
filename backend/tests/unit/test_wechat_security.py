"""WeChat access token is independent from Development Auth."""

from app.core.config import Settings
from app.core.security import (
    InvalidAccessTokenError,
    decode_access_token,
    decode_development_access_token,
    issue_access_token,
    issue_development_access_token,
)
import jwt
import pytest


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "dev_auth_enabled": True,
        "app_secret_key": "gate-b-test-secret-that-is-not-production",
        "jwt_issuer": "test-issuer",
        "jwt_audience": "test-audience",
        "jwt_access_token_ttl_seconds": 900,
    }
    values.update(overrides)
    return Settings(**values)


def test_wechat_token_does_not_contain_roles() -> None:
    token = issue_access_token(9, _settings(), auth_method="wechat")
    payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    assert payload["sub"] == "9"
    assert payload["auth_method"] == "wechat"
    assert "roles" not in payload
    assert "permissions" not in payload
    claims = decode_access_token(token, _settings())
    assert claims.auth_method == "wechat"
    assert claims.user_id == 9


def test_wechat_token_works_when_dev_auth_disabled() -> None:
    settings = _settings(dev_auth_enabled=False)
    token = issue_access_token(9, settings, auth_method="wechat")
    claims = decode_access_token(token, settings)
    assert claims.auth_method == "wechat"
    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(token, settings)


def test_development_decoder_still_rejects_wechat_method() -> None:
    settings = _settings()
    payload = jwt.decode(
        issue_development_access_token(42, settings),
        options={"verify_signature": False},
        algorithms=["HS256"],
    )
    payload["auth_method"] = "wechat"
    token = jwt.encode(
        payload,
        settings.app_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(token, settings)
