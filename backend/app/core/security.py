"""JWT security primitives for short-lived Development Access Tokens."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import jwt

from app.core.config import Settings
from app.core.time import utc_now

JWT_ALGORITHM = "HS256"
REQUIRED_ACCESS_TOKEN_CLAIMS = (
    "sub",
    "iss",
    "aud",
    "iat",
    "nbf",
    "exp",
    "jti",
    "token_type",
    "auth_method",
)


class AccessTokenError(Exception):
    """Base exception for a token that cannot authenticate a request."""


class AccessTokenExpiredError(AccessTokenError):
    """Token signature is valid but its expiration has passed."""


class InvalidAccessTokenError(AccessTokenError):
    """Token is malformed, untrusted, or has invalid claims."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: int
    issuer: str
    audience: str
    issued_at: int
    not_before: int
    expires_at: int
    jwt_id: str
    token_type: str
    auth_method: str


def issue_development_access_token(
    user_id: int,
    settings: Settings,
    *,
    now: datetime | None = None,
    jwt_id: str | None = None,
) -> str:
    if (
        not settings.dev_auth_enabled
        or settings.app_env not in {"development", "test"}
    ):
        raise ValueError("Development token signing is disabled")
    if user_id <= 0 or isinstance(user_id, bool):
        raise ValueError("user_id must be a positive integer")
    current = now or utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("JWT signing time must be timezone-aware")
    issued_at = int(current.astimezone(UTC).timestamp())
    expires_at = issued_at + settings.jwt_access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": jwt_id or uuid4().hex,
        "token_type": "access",
        "auth_method": "development",
    }
    secret = settings.app_secret_key.get_secret_value()
    if not secret:
        raise ValueError("APP_SECRET_KEY must not be empty")
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def issue_access_token(
    user_id: int,
    settings: Settings,
    *,
    auth_method: str,
    now: datetime | None = None,
    jwt_id: str | None = None,
) -> str:
    if auth_method == "development":
        return issue_development_access_token(
            user_id,
            settings,
            now=now,
            jwt_id=jwt_id,
        )
    if auth_method != "wechat":
        raise ValueError("Unsupported auth_method")
    if user_id <= 0 or isinstance(user_id, bool):
        raise ValueError("user_id must be a positive integer")
    current = now or utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("JWT signing time must be timezone-aware")
    issued_at = int(current.astimezone(UTC).timestamp())
    expires_at = issued_at + settings.jwt_access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": jwt_id or uuid4().hex,
        "token_type": "access",
        "auth_method": "wechat",
    }
    secret = settings.app_secret_key.get_secret_value()
    if not secret:
        raise ValueError("APP_SECRET_KEY must not be empty")
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(
    token: str,
    settings: Settings,
) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.app_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": list(REQUIRED_ACCESS_TOKEN_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenExpiredError("Access token has expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError("Access token is invalid") from exc

    auth_method = payload.get("auth_method")
    if auth_method == "development":
        return decode_development_access_token(token, settings)
    if auth_method != "wechat":
        raise InvalidAccessTokenError("Access token claims are invalid")
    try:
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.isdecimal():
            raise ValueError
        user_id = int(subject)
        if user_id <= 0 or str(user_id) != subject:
            raise ValueError
        issued_at = _integer_claim(payload, "iat")
        not_before = _integer_claim(payload, "nbf")
        expires_at = _integer_claim(payload, "exp")
        jwt_id = payload["jti"]
        if not isinstance(jwt_id, str) or not jwt_id:
            raise ValueError
        if payload["token_type"] != "access":
            raise ValueError
        if issued_at > expires_at or not_before > expires_at:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidAccessTokenError("Access token claims are invalid") from exc
    return AccessTokenClaims(
        user_id=user_id,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        jwt_id=jwt_id,
        token_type="access",
        auth_method="wechat",
    )


def decode_development_access_token(
    token: str,
    settings: Settings,
) -> AccessTokenClaims:
    if (
        not settings.dev_auth_enabled
        or settings.app_env not in {"development", "test"}
    ):
        raise InvalidAccessTokenError("Development token verification is disabled")
    try:
        payload = jwt.decode(
            token,
            settings.app_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": list(REQUIRED_ACCESS_TOKEN_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenExpiredError("Access token has expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError("Access token is invalid") from exc

    try:
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.isdecimal():
            raise ValueError
        user_id = int(subject)
        if user_id <= 0 or str(user_id) != subject:
            raise ValueError
        issued_at = _integer_claim(payload, "iat")
        not_before = _integer_claim(payload, "nbf")
        expires_at = _integer_claim(payload, "exp")
        jwt_id = payload["jti"]
        if not isinstance(jwt_id, str) or not jwt_id:
            raise ValueError
        if payload["token_type"] != "access":
            raise ValueError
        if payload["auth_method"] != "development":
            raise ValueError
        if issued_at > expires_at or not_before > expires_at:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidAccessTokenError("Access token claims are invalid") from exc

    return AccessTokenClaims(
        user_id=user_id,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        jwt_id=jwt_id,
        token_type="access",
        auth_method="development",
    )


def _integer_claim(payload: dict[str, object], name: str) -> int:
    value = payload[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value
