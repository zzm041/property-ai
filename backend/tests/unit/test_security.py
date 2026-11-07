"""JWT signing and verification security tests."""

from datetime import timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    AccessTokenExpiredError,
    InvalidAccessTokenError,
    decode_development_access_token,
    issue_development_access_token,
)
from app.core.time import utc_now


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


def _payload(token: str) -> dict[str, object]:
    return jwt.decode(
        token,
        options={"verify_signature": False},
        algorithms=["HS256"],
    )


def _encode_payload(
    payload: dict[str, object],
    settings: Settings,
    *,
    algorithm: str = "HS256",
) -> str:
    return jwt.encode(
        payload,
        settings.app_secret_key.get_secret_value(),
        algorithm=algorithm,
    )


def test_valid_development_access_token() -> None:
    settings = _settings()
    token = issue_development_access_token(42, settings, jwt_id="fixed-jti")

    claims = decode_development_access_token(token, settings)

    assert claims.user_id == 42
    assert claims.jwt_id == "fixed-jti"
    assert claims.token_type == "access"
    assert claims.auth_method == "development"
    assert claims.expires_at - claims.issued_at == 900


def test_token_signing_and_verification_require_dev_auth_enabled() -> None:
    enabled = _settings()
    token = issue_development_access_token(42, enabled)
    disabled = _settings(dev_auth_enabled=False)

    with pytest.raises(ValueError, match="signing is disabled"):
        issue_development_access_token(42, disabled)
    with pytest.raises(InvalidAccessTokenError, match="verification is disabled"):
        decode_development_access_token(token, disabled)


def test_token_contains_identity_not_authorization_snapshot() -> None:
    token = issue_development_access_token(42, _settings())
    payload = _payload(token)

    assert set(payload) == {
        "sub",
        "iss",
        "aud",
        "iat",
        "nbf",
        "exp",
        "jti",
        "token_type",
        "auth_method",
    }
    assert not {
        "role",
        "roles",
        "permission",
        "permissions",
        "scope",
        "tenant_id",
        "property_id",
    } & set(payload)


def test_expired_token_is_rejected() -> None:
    settings = _settings(jwt_access_token_ttl_seconds=60)
    token = issue_development_access_token(
        42,
        settings,
        now=utc_now() - timedelta(seconds=61),
    )

    with pytest.raises(AccessTokenExpiredError):
        decode_development_access_token(token, settings)


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", ""])
def test_malformed_token_is_rejected(token: str) -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(token, _settings())


def test_tampered_signature_is_rejected() -> None:
    settings = _settings()
    token = issue_development_access_token(42, settings)
    header, payload, signature = token.split(".")
    position = len(signature) // 2
    replacement = "a" if signature[position] != "a" else "b"
    tampered = (
        f"{header}.{payload}."
        f"{signature[:position]}{replacement}{signature[position + 1:]}"
    )

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(tampered, settings)


def test_wrong_issuer_is_rejected() -> None:
    settings = _settings()
    token = issue_development_access_token(42, settings)

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            token,
            _settings(jwt_issuer="wrong-issuer"),
        )


def test_wrong_audience_is_rejected() -> None:
    settings = _settings()
    token = issue_development_access_token(42, settings)

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            token,
            _settings(jwt_audience="wrong-audience"),
        )


def test_wrong_algorithm_is_rejected() -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    token = _encode_payload(payload, settings, algorithm="HS384")

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(token, settings)


def test_unsigned_algorithm_is_rejected() -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    token = jwt.encode(payload, key="", algorithm="none")

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(token, settings)


def test_wrong_token_type_is_rejected() -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    payload["token_type"] = "refresh"

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            _encode_payload(payload, settings),
            settings,
        )


@pytest.mark.parametrize("claim", ["iat", "nbf"])
def test_future_temporal_claim_is_rejected(claim: str) -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    payload[claim] = int(payload[claim]) + 300

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            _encode_payload(payload, settings),
            settings,
        )


def test_wrong_auth_method_is_rejected() -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    payload["auth_method"] = "wechat"

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            _encode_payload(payload, settings),
            settings,
        )


@pytest.mark.parametrize(
    "claim",
    ["sub", "iss", "aud", "iat", "nbf", "exp", "jti", "token_type", "auth_method"],
)
def test_every_required_claim_is_enforced(claim: str) -> None:
    settings = _settings()
    payload = _payload(issue_development_access_token(42, settings))
    del payload[claim]

    with pytest.raises(InvalidAccessTokenError):
        decode_development_access_token(
            _encode_payload(payload, settings),
            settings,
        )
