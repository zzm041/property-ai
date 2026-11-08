"""Opaque secret hashing for OTP and refresh tokens."""

import hashlib
import hmac
import secrets


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def generate_invite_token() -> str:
    return secrets.token_urlsafe(24)


def keyed_hash(value: str, *, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
