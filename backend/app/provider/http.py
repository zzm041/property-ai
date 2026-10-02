"""Shared HTTP mapping for production providers. Never log secrets."""

from collections.abc import Mapping
from ipaddress import ip_address
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger, safe_url_for_log
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = get_logger(__name__)


def _private_target(url: str) -> bool:
    """Classify local/private provider targets without performing DNS lookups.

    Compose service names are deliberately single-label (for example
    ``minio``).  They must not inherit a workstation's outbound proxy because
    storage requests contain private media and signed authorization headers.
    """
    try:
        host = urlsplit(url).hostname
        if host is None:
            return False
        normalized = host.rstrip(".").casefold()
        if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
            return True
        try:
            address = ip_address(normalized)
            return address.is_loopback or address.is_private or address.is_link_local
        except ValueError:
            return "." not in normalized
    except ValueError:
        return False


def build_http_client(*, url: str, timeout_seconds: float) -> httpx.Client:
    """Create a provider client without leaking loopback traffic to env proxies."""

    return httpx.Client(
        timeout=timeout_seconds,
        trust_env=not _private_target(url),
    )


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
    provider: str = "provider",
    operation: str = "request",
) -> Any:
    own_client = client is None
    # Local inference must never inherit a workstation's external proxy.
    # Explicitly injected clients remain caller-owned (e.g. test transports).
    # Non-loopback providers retain their existing environment/TLS behavior.
    http = client or build_http_client(url=url, timeout_seconds=timeout_seconds)
    started_at = time.perf_counter()
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
        _log_failure(provider, operation, "AI.TIMEOUT", started_at, url=url, exc=exc)
        raise ProviderTimeoutError("Provider timed out") from exc
    except httpx.HTTPError as exc:
        _log_failure(provider, operation, "AI.UNAVAILABLE", started_at, url=url, exc=exc)
        raise ProviderUnavailableError("Provider is unavailable") from exc
    finally:
        if own_client:
            http.close()
    if response.status_code == 429:
        _log_failure(provider, operation, "AI.RATE_LIMITED", started_at, url=url)
        raise ProviderRateLimitError("Provider rate limited")
    if response.status_code >= 500:
        _log_failure(provider, operation, "AI.UNAVAILABLE", started_at, url=url)
        raise ProviderUnavailableError("Provider is unavailable")
    if response.status_code >= 400:
        _log_failure(provider, operation, "AI.INVALID_OUTPUT", started_at, url=url)
        raise ProviderInvalidOutputError("Provider rejected the request")
    if not response.content:
        _log_failure(provider, operation, "AI.INVALID_OUTPUT", started_at, url=url)
        raise ProviderInvalidOutputError("Provider returned an empty response")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        _log_failure(provider, operation, "AI.INVALID_OUTPUT", started_at, url=url, exc=exc)
        raise ProviderInvalidOutputError("Provider returned invalid JSON") from exc
    logger.info(
        "provider_succeeded",
        provider=provider,
        operation=operation,
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return payload


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


def _log_failure(
    provider: str,
    operation: str,
    error_code: str,
    started_at: float,
    *,
    url: str | None = None,
    exc: BaseException | None = None,
) -> None:
    logger.warning(
        "provider_failed",
        provider=provider,
        operation=operation,
        error_code=error_code,
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        url=safe_url_for_log(url) if url else None,
        error_type=type(exc).__name__ if exc is not None else None,
    )
