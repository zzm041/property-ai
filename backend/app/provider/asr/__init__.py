"""ASR Provider. Failure must not prevent WorkOrder creation."""

from dataclasses import dataclass
from typing import Protocol
from hashlib import sha256
import json

import httpx

from app.core.logging import get_logger
from app.provider.asr.pcm import PCM_SAMPLE_RATE, to_pcm_wav
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.http import request_json
from app.provider.storage import StorageProvider
from app.provider.usage import response_token_usage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    confidence: float
    model_version: str
    token_usage: int | None = None


class ASRProvider(Protocol):
    model_version: str

    def transcribe(self, *, storage_key: str, mime_type: str) -> Transcript:
        """Return transcript text. Original audio remains even if this fails."""


class DevelopmentASRProvider:
    model_version = "dev-asr-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    @property
    def checkpoint_fingerprint(self) -> str:
        value = {"adapter": "development-asr-v1", "model": self.model_version,
                 "behavior": self.behavior}
        return sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

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
        model_revision: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_revision = model_revision
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._storage = storage
        self._client = client
        self.model_version = model or self.model_version

    @property
    def checkpoint_fingerprint(self) -> str:
        # A declared deployment revision fences alias replacement; the client
        # cannot discover a server change that the operator does not declare.
        value = {"adapter": "http-asr-transcription-v1", "endpoint": self.base_url,
                 "model": self.model, "model_version": self.model_version,
                 **({"model_revision": self.model_revision} if self.model_revision else {})}
        return sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def transcribe(self, *, storage_key: str, mime_type: str) -> Transcript:
        if not self.base_url or not self._api_key:
            raise ProviderUnavailableError("ASR is unavailable")
        content = self._storage.get_object(storage_key)
        # The adapter only reads PCM WAV; phones record mp3/m4a/webm.
        audio = to_pcm_wav(content, mime_type)
        if audio.transcoded:
            logger.info(
                "asr_audio_transcoded",
                source_mime_type=mime_type,
                source_bytes=audio.source_bytes,
                pcm_bytes=len(audio.content),
                duration_seconds=round(audio.duration_seconds, 3),
                sample_rate=PCM_SAMPLE_RATE,
            )
        payload = request_json(
            method="POST",
            url=f"{self.base_url}/audio/transcriptions",
            timeout_seconds=self.timeout_seconds,
            headers={"Authorization": f"Bearer {self._api_key}"},
            data={"model": self.model},
            files={"file": ("audio.wav", audio.content, "audio/wav")},
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
            token_usage=response_token_usage(payload),
        )
