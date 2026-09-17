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
from app.core.logging import get_logger, redact_value

logger = get_logger(__name__)

_ALLOWED_OPERATIONS = frozenset(
    {
        "app.worker.tasks.analyze_work_order_task",
        "app.worker.tasks.ingest_knowledge_task",
        "app.worker.tasks.deliver_notification_task",
    }
)
_LEASE_SECONDS = 120
_MAX_ARG_STRING = 128
STATUS_RECORDED = "RECORDED"
STATUS_REPLAY_ENQUEUED = "REPLAY_ENQUEUED"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_IGNORED_EXPIRED = "IGNORED_EXPIRED"


def _key(settings: Settings) -> str:
    return f"property_ai:{settings.app_env}:failed_tasks"


def _hash_key(settings: Settings) -> str:
    return f"{_key(settings)}:records"


def _open_key(settings: Settings) -> str:
    return f"{_key(settings)}:open"


def _lease_key(settings: Settings, record_id: str) -> str:
    return f"{_key(settings)}:lease:{record_id}"


def _sanitize_args(args: tuple[Any, ...] | list[Any] | None) -> list[Any]:
    cleaned: list[Any] = []
    for item in args or ():
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, int) and not isinstance(item, bool):
            cleaned.append(item)
            continue
        if isinstance(item, str) and len(item) <= _MAX_ARG_STRING:
            lowered = item.casefold()
            if any(token in lowered for token in ("bearer ", "eyj", "secret", "password")):
                cleaned.append("[redacted]")
            else:
                cleaned.append(item)
    return cleaned


def _sanitize_extra(extra: dict[str, Any] | None) -> dict[str, Any]:
    return redact_value(dict(extra or {}))


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
        "args": _sanitize_args(args),
        "extra": _sanitize_extra(extra),
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queue": resolved.resolved_celery_queue,
        "dlq": resolved.resolved_celery_dlq,
        "status": STATUS_RECORDED,
        "replay_task_id": None,
        "replayed": False,
    }
    redis_ok = False
    dlq_ok = False
    try:
        client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
        pipe = client.pipeline()
        pipe.hset(_hash_key(resolved), record_id, json.dumps(payload, ensure_ascii=False))
        pipe.zadd(_open_key(resolved), {record_id: datetime.now(timezone.utc).timestamp()})
        pipe.execute()
        client.close()
        redis_ok = True
    except Exception:
        logger.warning(
            "failed_task_record_unavailable",
            operation=operation,
            error_code=error_code,
        )
    try:
        _publish_dlq(resolved, payload)
        dlq_ok = True
    except Exception:
        logger.warning("failed_task_dlq_publish_unavailable", dlq=resolved.resolved_celery_dlq)
    if not redis_ok and not dlq_ok:
        return None
    logger.error(
        "failed_task_recorded",
        task_id=task_id,
        operation=operation,
        error_code=error_code,
        dlq=resolved.resolved_celery_dlq,
        redis_ok=redis_ok,
        dlq_ok=dlq_ok,
    )
    return record_id


def list_failed_tasks(settings: Settings | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    resolved = settings or get_settings()
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        record_ids = client.zrevrange(_open_key(resolved), 0, max(0, limit - 1))
        records: list[dict[str, Any]] = []
        for raw_id in record_ids:
            raw = client.hget(_hash_key(resolved), raw_id)
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return records
    finally:
        client.close()


def mark_failed_task_succeeded(task_id: str, settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    for record in list_failed_tasks(resolved, limit=500):
        if record.get("replay_task_id") == task_id or record.get("task_id") == task_id:
            record["status"] = STATUS_SUCCEEDED
            _store_record(resolved, record)
            return


def replay_failed_task(record_id: str, settings: Settings | None = None) -> bool:
    """Re-enqueue the original Celery task. Does not mark the business outcome success."""

    resolved = settings or get_settings()
    records = {item.get("record_id"): item for item in list_failed_tasks(resolved, limit=500)}
    match = records.get(record_id) or next(
        (item for item in records.values() if item.get("task_id") == record_id),
        None,
    )
    if match is None:
        return False
    operation = str(match.get("operation") or "")
    if operation not in _ALLOWED_OPERATIONS:
        logger.error("failed_task_replay_unknown_operation", operation=operation)
        return False
    if match.get("queue") not in {None, resolved.resolved_celery_queue}:
        logger.error("failed_task_replay_queue_mismatch", operation=operation)
        return False
    args = tuple(_sanitize_args(match.get("args") or ()))
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        leased = client.set(
            _lease_key(resolved, str(match["record_id"])),
            "1",
            nx=True,
            ex=_LEASE_SECONDS,
        )
        if not leased:
            logger.warning("failed_task_replay_in_progress", record_id=match["record_id"])
            return False
    finally:
        client.close()
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
    async_result = mapping[operation].apply_async(
        args=args,
        queue=resolved.resolved_celery_queue,
    )
    match["status"] = STATUS_REPLAY_ENQUEUED
    match["replay_task_id"] = str(async_result.id)
    match["replayed"] = False
    _store_record(resolved, match)
    logger.info(
        "failed_task_replay_enqueued",
        record_id=match["record_id"],
        operation=operation,
        replay_task_id=str(async_result.id),
    )
    return True


def _store_record(settings: Settings, payload: dict[str, Any]) -> None:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        client.hset(
            _hash_key(settings),
            str(payload["record_id"]),
            json.dumps(payload, ensure_ascii=False),
        )
        if payload.get("status") == STATUS_SUCCEEDED:
            client.zrem(_open_key(settings), payload["record_id"])
    finally:
        client.close()


def _publish_dlq(settings: Settings, payload: dict[str, Any]) -> None:
    exchange = Exchange(settings.resolved_celery_dlq, type="direct", durable=True)
    queue = Queue(
        settings.resolved_celery_dlq,
        exchange=exchange,
        routing_key=settings.resolved_celery_dlq,
        durable=True,
    )
    with Connection(
        settings.celery_broker_url,
        connect_timeout=2,
        transport_options={"confirm_publish": True},
    ) as connection:
        queue.maybe_bind(connection)
        queue.declare()
        with Producer(connection, serializer="json") as producer:
            producer.publish(
                payload,
                exchange=exchange,
                routing_key=settings.resolved_celery_dlq,
                serializer="json",
                declare=[queue],
                delivery_mode=2,
                retry=True,
            )
