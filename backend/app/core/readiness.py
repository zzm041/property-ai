"""Bounded readiness probes with Critical/Degraded classification."""

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import redis
from kombu import Connection
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import get_engine, verify_utc_session
from app.core.logging import get_logger

logger = get_logger(__name__)

Probe = Callable[[Settings], Any]
NOT_CHECKED = object()
AI_PROVIDER_NOT_CONFIGURED = "not_configured"
# t221: /readyz used to answer `ready` with the frozen AI adapter stopped, because
# every model provider was registered as NOT_CHECKED (`live_vendor_not_probed`).
# A probe now answers ok|down|not_configured for each *configured* provider; a
# configured provider that cannot be reached degrades readiness with a reason.
AI_PROVIDER_TIMEOUT_SECONDS = 2.0
AI_PROVIDER_TARGETS: dict[str, tuple[str, str, str]] = {
    "llm": ("llm_provider", "llm_base_url", "llm_api_key"),
    "vlm": ("vlm_provider", "vlm_base_url", "vlm_api_key"),
    "asr": ("asr_provider", "asr_base_url", "asr_api_key"),
    "embedding": ("embedding_provider", "embedding_endpoint", "embedding_api_key"),
    "rerank": ("reranker_provider", "reranker_endpoint", "reranker_api_key"),
}


def ai_provider_endpoint(settings: Settings, name: str) -> tuple[str, str]:
    """Return (endpoint, bearer token) for a *configured* provider, else ("", "").

    `development` providers never leave the process, so they are not configured
    endpoints and must not be reported as down (that would be a false alarm).
    """

    provider_attr, endpoint_attr, key_attr = AI_PROVIDER_TARGETS[name]
    provider = getattr(settings, provider_attr)
    endpoint = str(getattr(settings, endpoint_attr) or "").strip()
    if provider != "production" or not endpoint:
        return "", ""
    key = settings.resolve_local_ai_key(getattr(settings, key_attr), endpoint)
    return endpoint, key


def ai_health_url(endpoint: str) -> str:
    """Map a provider base URL (…/v1) to the launcher's /healthz route."""

    base = endpoint.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/healthz"


def _dependency_name(name: str) -> str:
    """Public provider name -> probe table key (only `rerank` differs)."""

    return "reranker" if name == "rerank" else name


def probe_ai_endpoint(settings: Settings, name: str) -> str:
    """ok | not_configured for the ready path; raises when a configured one is down."""

    endpoint, key = ai_provider_endpoint(settings, name)
    if not endpoint:
        return AI_PROVIDER_NOT_CONFIGURED
    request = urllib.request.Request(ai_health_url(endpoint), method="GET")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(
        request, timeout=AI_PROVIDER_TIMEOUT_SECONDS
    ) as response:
        if response.status >= 400:
            raise RuntimeError(f"health probe returned {response.status}")
    return "up"


def probe_mysql(settings: Settings) -> None:
    timeout = max(1, round(settings.readiness_timeout_seconds))
    engine = get_engine(settings.active_database_url, timeout)
    with engine.connect() as connection:
        verify_utc_session(connection)
        connection.execute(text("SELECT 1")).scalar_one()


def probe_redis(settings: Settings) -> None:
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )
    try:
        client.ping()
    finally:
        client.close()


def probe_milvus(settings: Settings) -> None:
    from pymilvus import connections, utility

    alias = "readiness"
    try:
        connections.connect(
            alias=alias,
            uri=settings.milvus_uri,
            timeout=settings.readiness_timeout_seconds,
        )
        utility.list_collections(using=alias)
    finally:
        try:
            connections.disconnect(alias)
        except Exception:  # noqa: BLE001 - probe cleanup
            pass


def probe_rabbitmq(settings: Settings) -> None:
    with Connection(
        settings.celery_broker_url,
        connect_timeout=settings.readiness_timeout_seconds,
    ) as connection:
        connection.ensure_connection(
            max_retries=0,
            timeout=settings.readiness_timeout_seconds,
        )


def queue_depth(settings: Settings) -> int | None:
    """Pending messages on the analysis queue, or None when it is unreachable.

    PA-104-D: a degraded broker used to be invisible (readyz said 200/degraded
    with no depth anywhere). Depth is published next to the probe result so a
    backlog is observable without opening RabbitMQ.
    """

    try:
        with Connection(
            settings.celery_broker_url,
            connect_timeout=settings.readiness_timeout_seconds,
        ) as connection:
            queue = connection.default_channel.queue_declare(
                queue=settings.resolved_celery_queue,
                passive=True,
            )
            return int(queue.message_count)
    except Exception as exc:  # noqa: BLE001 - dependency clients raise many families
        logger.warning(
            "queue_depth_unavailable",
            queue=settings.resolved_celery_queue,
            error_type=type(exc).__name__,
        )
        return None


def _run_probe(name: str, critical: bool, probe: Probe | object, settings: Settings) -> dict[str, Any]:
    if probe is NOT_CHECKED:
        return {
            "status": "not_checked",
            "critical": critical,
            "checked_at": None,
            "reason": "live_vendor_not_probed",
        }
    started_at = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        outcome = probe(settings)  # type: ignore[misc]
    except Exception as exc:  # Dependency clients raise different exception families.
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.warning(
            "readiness_probe_failed",
            dependency=name,
            critical=critical,
            error_type=type(exc).__name__,
            latency_ms=latency_ms,
        )
        return {
            "status": "down",
            "critical": critical,
            "latency_ms": latency_ms,
            "checked_at": checked_at,
        }

    # A probe may vote `not_configured` instead of pretending to be up.
    status = outcome if isinstance(outcome, str) else "up"
    return {
        "status": status,
        "critical": critical,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "checked_at": checked_at,
    }


def check_readiness(
    settings: Settings,
    probes: dict[str, tuple[bool, Any]] | None = None,
    *,
    include_queue: bool = True,
) -> tuple[dict[str, Any], int]:
    """Return dependency details and the HTTP status dictated by criticality."""

    selected_probes = probes or {
        "mysql": (True, probe_mysql),
        # PA-104-D: the analysis queue and its cache are on the business path.
        # While they were non-critical the endpoint answered 200/degraded, so a
        # load balancer kept routing to an instance that could not enqueue work.
        "redis": (True, probe_redis),
        "rabbitmq": (True, probe_rabbitmq),
        # t221: probing a configured endpoint is what makes `ready` truthful;
        # an unconfigured provider reports `not_configured`, never `up`.
        "llm": (False, partial(probe_ai_endpoint, name="llm")),
        "vlm": (False, partial(probe_ai_endpoint, name="vlm")),
        "asr": (False, partial(probe_ai_endpoint, name="asr")),
        "embedding": (False, partial(probe_ai_endpoint, name="embedding")),
        "reranker": (False, partial(probe_ai_endpoint, name="rerank")),
        "storage": (False, NOT_CHECKED),
        "milvus": (False, probe_milvus),
    }

    with ThreadPoolExecutor(max_workers=len(selected_probes)) as executor:
        futures = {
            name: executor.submit(_run_probe, name, critical, probe, settings)
            for name, (critical, probe) in selected_probes.items()
        }
        dependencies = {name: future.result() for name, future in futures.items()}

    critical_down = any(
        result["critical"] and result["status"] == "down"
        for result in dependencies.values()
    )
    any_down = any(result["status"] == "down" for result in dependencies.values())

    media_pipeline = settings.media_pipeline()
    # D6: an unacknowledged demo scanner is a real gap in the media chain, so it
    # degrades readiness instead of being reported as a healthy pipeline.
    media_gap = bool(media_pipeline.get("demo_stub_unacknowledged"))

    # t221: `ai_providers` is the honest one-line answer per provider.
    ai_providers: dict[str, str] = {}
    ai_provider_details: dict[str, dict[str, Any]] = {}
    degraded_reasons: list[str] = []
    for name in AI_PROVIDER_TARGETS:
        # `rerank` is the public provider name; the dependency probe is `reranker`.
        entry = dependencies.get(name) or dependencies.get(_dependency_name(name))
        if entry is None:
            continue
        state = "ok" if entry["status"] == "up" else entry["status"]
        if state not in {"ok", AI_PROVIDER_NOT_CONFIGURED}:
            state = "down"
        ai_providers[name] = state
        detail: dict[str, Any] = {"status": state}
        if "latency_ms" in entry:
            detail["latency_ms"] = entry["latency_ms"]
        if entry.get("reason"):
            detail["reason"] = entry["reason"]
        ai_provider_details[name] = detail
        if state == "down":
            degraded_reasons.append(f"ai_provider_down:{name}")

    ai_dependency_names = {_dependency_name(provider) for provider in AI_PROVIDER_TARGETS}
    for name, entry in dependencies.items():
        if name in ai_dependency_names or name in AI_PROVIDER_TARGETS:
            continue
        if entry["status"] == "down":
            degraded_reasons.append(f"dependency_down:{name}")
    if media_gap:
        degraded_reasons.append("media_pipeline:demo_stub_unacknowledged")

    if critical_down:
        status = "not_ready"
        http_status = 503
    elif any_down or media_gap:
        status = "degraded"
        http_status = 200
    else:
        status = "ready"
        http_status = 200

    return {
        "status": status,
        # PA-117: a missing work-order credential used to be invisible until the
        # first analysis call failed; readiness now states it. Secrets are not
        # included, and the overall status is untouched: api/beat/publisher never
        # call the model, so degrading their readiness would be a false alarm.
        "ai_credentials": settings.ai_credentials(),
        # PA-163: with no scanner configured media can never become usable, and the
        # only symptom used to be an empty description suggestion. Report the cause.
        # D6: the development stub is reported as `demo_stub` (unverified media),
        # never as a scanned pipeline.
        "media_pipeline": media_pipeline,
        "dependencies": dependencies,
        # t221: one line per model provider (ok|down|not_configured).
        "ai_providers": ai_providers,
        "ai_provider_details": ai_provider_details,
        "degraded_reasons": degraded_reasons,
        "queue": {
            "name": settings.resolved_celery_queue,
            "depth": queue_depth(settings) if include_queue else None,
        },
    }, http_status
