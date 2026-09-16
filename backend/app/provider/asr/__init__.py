"""ASR Provider. Failure must not prevent WorkOrder creation."""

from dataclasses import dataclass
from typing import Protocol

from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    confidence: float
    model_version: str


class ASRProvider(Protocol):
    model_version: str

    def transcribe(self, *, storage_key: str, mime_type: str) -> Transcript:
        """Return transcript text. Original audio remains even if this fails."""


class DevelopmentASRProvider:
    model_version = "dev-asr-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def transcribe(self, *, storage_key: str, mime_type: str) -> Transcript:
        del storage_key, mime_type
        if self.behavior == "timeout":
            raise ProviderTimeoutError("ASR timed out")
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("ASR is unavailable")
        return Transcript(
            text="The kitchen outlet is sparking",
            confidence=0.91,
            model_version=self.model_version,
        )
