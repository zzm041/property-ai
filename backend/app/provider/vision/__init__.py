"""VLM / Vision Provider. Results are suggestions only."""

from dataclasses import dataclass
from typing import Protocol
import base64

import httpx

from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.http import parse_json_object, request_json
from app.provider.storage import StorageProvider
from app.provider.usage import response_token_usage


@dataclass(frozen=True, slots=True)
class VisionInsight:
    description: str
    labels: tuple[str, ...]
    confidence: float
    model_version: str
    token_usage: int | None = None


class VLMProvider(Protocol):
    model_version: str

    def inspect_image(self, *, storage_key: str, mime_type: str) -> VisionInsight:
        """Describe a stored image. Must not decide dispatch or closure."""


VisionProvider = VLMProvider


class DevelopmentVLMProvider:
    model_version = "dev-vlm-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def inspect_image(self, *, storage_key: str, mime_type: str) -> VisionInsight:
        del storage_key, mime_type
        if self.behavior == "timeout":
            raise ProviderTimeoutError("VLM timed out")
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("VLM is unavailable")
        return VisionInsight(
            description="Scorched outlet cover with visible spark marks",
            labels=("electrical", "burn_mark"),
            confidence=0.74,
            model_version=self.model_version,
        )


class QwenVLMProvider:
    """Qwen-VL compatible adapter. Reads only the authorized storage object."""

    model_version = "qwen-vl"
    prompt_version = "repair-photo-v4"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float,
        storage: StorageProvider,
        client: httpx.Client | None = None,
        disable_thinking: bool = False,
        max_output_tokens: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._storage = storage
        self._client = client
        self.model_version = model or self.model_version
        self._disable_thinking = disable_thinking
        self._max_output_tokens = max_output_tokens

    def inspect_image(self, *, storage_key: str, mime_type: str) -> VisionInsight:
        if not self.base_url or not self._api_key:
            raise ProviderUnavailableError("VLM is unavailable")
        content = self._storage.get_object(storage_key)
        encoded = base64.b64encode(content).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"
        payload = request_json(
            method="POST",
            url=f"{self.base_url}/chat/completions",
            timeout_seconds=self.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json_body={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "JSON:description(中文8字物体通称及明显破损，禁止完好/正常/细类),"
                                    "labels:[],confidence"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                **({"reasoning_effort": "none"} if self._disable_thinking else {}),
                **({"max_tokens": self._max_output_tokens} if self._max_output_tokens is not None else {}),
            },
            client=self._client,
            provider="vlm",
            operation="inspect_image",
        )
        try:
            raw = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderInvalidOutputError("VLM returned invalid JSON") from exc
        parsed = parse_json_object(str(raw))
        description = parsed.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ProviderInvalidOutputError("VLM returned an empty description")
        labels = parsed.get("labels") or ()
        if isinstance(labels, str):
            labels = (labels,)
        confidence = parsed.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ProviderInvalidOutputError("VLM returned invalid JSON") from exc
        return VisionInsight(
            description=description,
            labels=tuple(str(item) for item in labels),
            confidence=confidence_value,
            model_version=self.model_version,
            token_usage=response_token_usage(payload),
        )
