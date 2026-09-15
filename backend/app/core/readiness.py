"""Bounded readiness probes with Critical/Degraded classification."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pymysql
import redis
from kombu import Connection
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

Probe = Callable[[Settings], None]


def probe_mysql(settings: Settings) -> None:
    url = make_url(settings.active_database_url)
    timeout = max(1, round(settings.readiness_timeout_seconds))
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.database,
        charset=url.query.get("charset", "utf8mb4"),
        connect_timeout=timeout,
        read_timeout=timeout,
        write_timeout=timeout,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    finally:
        connection.close()


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


def probe_rabbitmq(settings: Settings) -> None:
    with Connection(
        settings.celery_broker_url,
        connect_timeout=settings.readiness_timeout_seconds,
    ) as connection:
        connection.ensure_connection(
            max_retries=0,
            timeout=settings.readiness_timeout_seconds,
        )


def _run_probe(name: str, critical: bool, probe: Probe, settings: Settings) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        probe(settings)
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
        }

    return {
        "status": "up",
        "critical": critical,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }


def check_readiness(
    settings: Settings,
    probes: dict[str, tuple[bool, Probe]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Return dependency details and the HTTP status dictated by criticality."""

    selected_probes = probes or {
        "mysql": (True, probe_mysql),
        "redis": (False, probe_redis),
        "rabbitmq": (False, probe_rabbitmq),
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
