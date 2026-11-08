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
) -> tuple[dict[str, Any], int]:
    """Return dependency details and the HTTP status dictated by criticality."""

    selected_probes = probes or {
        "mysql": (True, probe_mysql),
        "redis": (False, probe_redis),
        "rabbitmq": (False, probe_rabbitmq),
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

    if critical_down:
        status = "not_ready"
        http_status = 503
    elif any_down:
        status = "degraded"
        http_status = 200
    else:
        status = "ready"
        http_status = 200

    return {"status": status, "dependencies": dependencies}, http_status
