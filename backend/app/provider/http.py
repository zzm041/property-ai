"""Shared HTTP mapping for production providers. Never log secrets."""

from collections.abc import Mapping
import json
import logging
from typing import Any

import httpx

from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def request_json(
    *,
    method: str,
    url: str,
    timeout_seconds: float,
    headers: Mapping[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
    data: Any = None,
    files: Any = None,
    params: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> Any:
    own_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http.request(
            method,
            url,
            headers=dict(headers or {}),
            json=json_body,
            data=data,
            files=files,
            params=params,
        )
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("Provider timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError("Provider is unavailable") from exc
    finally:
        if own_client:
            http.close()
    if response.status_code == 429:
        raise ProviderRateLimitError("Provider rate limited")
    if response.status_code >= 500:
        raise ProviderUnavailableError("Provider is unavailable")
    if response.status_code >= 400:
        raise ProviderInvalidOutputError("Provider rejected the request")
    if not response.content:
        raise ProviderInvalidOutputError("Provider returned an empty response")
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise ProviderInvalidOutputError("Provider returned invalid JSON") from exc


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderInvalidOutputError("Provider returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderInvalidOutputError("Provider returned invalid JSON")
    return payload
