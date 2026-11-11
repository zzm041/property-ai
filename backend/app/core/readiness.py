"""Bounded readiness probes with Critical/Degraded classification."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import redis
from kombu import Connection
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import get_engine, verify_utc_session
from app.core.logging import get_logger

logger = get_logger(__name__)

Probe = Callable[[Settings], None]
NOT_CHECKED = object()


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
        probe(settings)  # type: ignore[misc]
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

    return {
        "status": "up",
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
        "llm": (False, NOT_CHECKED),
        "vlm": (False, NOT_CHECKED),
        "asr": (False, NOT_CHECKED),
        "embedding": (False, NOT_CHECKED),
        "reranker": (False, NOT_CHECKED),
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
        "queue": {
            "name": settings.resolved_celery_queue,
            "depth": queue_depth(settings) if include_queue else None,
        },
    }, http_status
