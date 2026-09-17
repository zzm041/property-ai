"""Inspectable failed-task records without a new database migration.

Permanent Celery failures are written to Redis and published onto the
environment DLQ for queue-depth inspection. This is not AMQP DLX topology.
Replay is explicit via `python -m app.cli.replay_failed_task`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import redis
from kombu import Connection, Exchange, Producer, Queue

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_RECORDS = 200


def _key(settings: Settings) -> str:
    return f"property_ai:{settings.app_env}:failed_tasks"


def record_failed_task(
    settings: Settings | None = None,
    *,
    task_id: str | None,
    operation: str | None,
    error_code: str,
    extra: dict[str, Any] | None = None,
    args: tuple[Any, ...] | list[Any] | None = None,
) -> str | None:
    resolved = settings or get_settings()
    record_id = task_id or f"enqueue-{uuid4().hex}"
    payload = {
        "record_id": record_id,
        "task_id": task_id,
        "operation": operation,
        "error_code": error_code,
        "args": list(args or ()),
        "extra": dict(extra or {}),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queue": resolved.resolved_celery_queue,
        "dlq": resolved.resolved_celery_dlq,
        "replayed": False,
    }
    try:
        client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
        client.lpush(_key(resolved), json.dumps(payload, ensure_ascii=False))
        client.ltrim(_key(resolved), 0, _MAX_RECORDS - 1)
        client.close()
    except Exception:
        logger.warning(
            "failed_task_record_unavailable",
            operation=operation,
            error_code=error_code,
        )
        return None
    _publish_dlq(resolved, payload)
    logger.error(
        "failed_task_recorded",
        task_id=task_id,
        operation=operation,
        error_code=error_code,
        dlq=resolved.resolved_celery_dlq,
    )
    return record_id


def list_failed_tasks(settings: Settings | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    resolved = settings or get_settings()
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        raw = client.lrange(_key(resolved), 0, max(0, limit - 1))
    finally:
        client.close()
    records: list[dict[str, Any]] = []
    for item in raw:
        try:
            records.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    return records


def replay_failed_task(record_id: str, settings: Settings | None = None) -> bool:
    """Re-enqueue the original Celery task by operation name. Operator-only."""

    resolved = settings or get_settings()
    records = list_failed_tasks(resolved, limit=_MAX_RECORDS)
    match = next((item for item in records if item.get("record_id") == record_id or item.get("task_id") == record_id), None)
    if match is None:
        return False
    operation = str(match.get("operation") or "")
    args = tuple(match.get("args") or ())
    from app.worker.tasks import (
        analyze_work_order_task,
        deliver_notification_task,
        ingest_knowledge_task,
    )

    mapping = {
        "app.worker.tasks.analyze_work_order_task": analyze_work_order_task,
        "app.worker.tasks.ingest_knowledge_task": ingest_knowledge_task,
        "app.worker.tasks.deliver_notification_task": deliver_notification_task,
    }
    task = mapping.get(operation)
    if task is None:
        logger.error("failed_task_replay_unknown_operation", operation=operation)
        return False
    task.apply_async(args=args, queue=resolved.resolved_celery_queue)
    logger.info("failed_task_replayed", record_id=record_id, operation=operation)
    return True


def _publish_dlq(settings: Settings, payload: dict[str, Any]) -> None:
    try:
        exchange = Exchange(settings.resolved_celery_dlq, type="direct")
        queue = Queue(
            settings.resolved_celery_dlq,
            exchange=exchange,
            routing_key=settings.resolved_celery_dlq,
        )
        with Connection(settings.celery_broker_url, connect_timeout=2) as connection:
            queue.maybe_bind(connection)
            queue.declare()
            with Producer(connection) as producer:
                producer.publish(
                    payload,
                    exchange=exchange,
                    routing_key=settings.resolved_celery_dlq,
                    serializer="json",
                    declare=[queue],
                )
    except Exception:
        logger.warning("failed_task_dlq_publish_unavailable", dlq=settings.resolved_celery_dlq)
