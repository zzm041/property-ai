"""Replaceable high-risk identity proof interface.

Providers return only opaque references. Raw documents, face images and biometric
templates must never be returned to or persisted by this application.
"""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IdentityProofResult:
    provider: str
    reference: str
    subject_reference: str
    verified: bool


class IdentityProofProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def verify(
        self,
        *,
        assertion: str,
        purpose: str,
        phone_e164: str,
    ) -> IdentityProofResult: ...


class UnavailableIdentityProofProvider:
    @property
    def available(self) -> bool:
        return False

    def verify(self, *, assertion: str, purpose: str, phone_e164: str) -> IdentityProofResult:
        raise RuntimeError("No trusted identity proof provider is configured")


class IsolatedTestIdentityProofProvider:
    """Signed synthetic proof accepted only by the exact AUTH001 test runtime.

    Tokens are issued by server-side test fixtures. The client cannot submit a
    boolean to manufacture a successful verification.
    """

    available = True

    def __init__(self, *, secret: str, ttl_seconds: int = 300, clock=None) -> None:
        if not secret:
            raise ValueError("test proof signing secret is required")
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def issue(self, *, purpose: str, phone_e164: str, subject_reference: str) -> str:
        now = self._clock()
        payload = {
            "iss": "AUTH001_TEST_FIXTURE",
            "purpose": purpose,
            "phone": phone_e164,
            "subject": subject_reference,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl_seconds)).timestamp()),
            "nonce": secrets.token_hex(16),
        }
        encoded = self._encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = self._encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, *, assertion: str, purpose: str, phone_e164: str) -> IdentityProofResult:
        try:
            encoded, supplied = assertion.split(".", 1)
            expected = self._encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("signature")
            payload = json.loads(self._decode(encoded))
            now = int(self._clock().timestamp())
            if (
                payload.get("iss") != "AUTH001_TEST_FIXTURE"
                or payload.get("purpose") != purpose
                or payload.get("phone") != phone_e164
                or not isinstance(payload.get("subject"), str)
                or int(payload.get("iat", 0)) > now
                or int(payload.get("exp", 0)) <= now
            ):
                raise ValueError("claims")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return IdentityProofResult(
                provider="AUTH001_TEST_FIXTURE",
                reference="rejected",
                subject_reference="rejected",
                verified=False,
            )
        digest = hashlib.sha256(assertion.encode()).hexdigest()
        return IdentityProofResult(
            provider="AUTH001_TEST_FIXTURE",
            reference=f"proof:{digest}",
            subject_reference=payload["subject"],
            verified=True,
        )

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).decode("utf-8")
