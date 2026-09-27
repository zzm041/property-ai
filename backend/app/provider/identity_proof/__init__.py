"""Replaceable high-risk identity proof interface.

Providers return only opaque references. Raw documents, face images and biometric
templates must never be returned to or persisted by this application.
"""

from dataclasses import dataclass
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
