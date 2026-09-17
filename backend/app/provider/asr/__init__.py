"""ASR Provider. Failure must not prevent WorkOrder creation."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.http import request_json
from app.provider.storage import StorageProvider


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


class HttpASRProvider:
    """OpenAI-compatible transcriptions adapter. Auth stays in settings."""

    model_version = "asr"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        storage: StorageProvider,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._storage = storage
        self._client = client
        self.model_version = model or self.model_version

    def transcribe(self, *, storage_key: str, mime_type: str) -> Transcript:
        if not self.base_url or not self._api_key:
            raise ProviderUnavailableError("ASR is unavailable")
        content = self._storage.get_object(storage_key)
        extension = mime_type.split("/", 1)[-1] or "bin"
        payload = request_json(
            method="POST",
            url=f"{self.base_url}/audio/transcriptions",
            timeout_seconds=self.timeout_seconds,
            headers={"Authorization": f"Bearer {self._api_key}"},
            data={"model": self.model},
            files={"file": (f"audio.{extension}", content, mime_type)},
            client=self._client,
            provider="asr",
            operation="transcribe",
        )
        text = payload.get("text") or payload.get("transcript")
        if not isinstance(text, str) or not text.strip():
            raise ProviderInvalidOutputError("ASR returned an empty response")
        confidence = payload.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        return Transcript(
            text=text.strip(),
            confidence=confidence_value,
            model_version=self.model_version,
        )
