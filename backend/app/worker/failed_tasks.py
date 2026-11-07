"""Inspectable failed-task records without a new database migration.

Permanent Celery failures are written to Redis and published onto the
environment DLQ for queue-depth inspection. This is not AMQP DLX topology.
Replay is explicit via `python -m app.cli.replay_failed_task`.

Celery task_success is not business SUCCEEDED. Business outcome is recorded
from the worker after the domain result is known.
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
_OPERATION_ARGS: dict[str, tuple[tuple[str, type], ...]] = {
    "app.worker.tasks.analyze_work_order_task": (
        ("work_order_id", int),
        ("request_id", str),
    ),
    "app.worker.tasks.ingest_knowledge_task": (
        ("document_id", int),
        ("version", int),
        ("request_id", str),
    ),
    "app.worker.tasks.deliver_notification_task": (("delivery_id", int),),
}
_LEASE_SECONDS = 120
_CAS_RETRIES = 3
_MAX_ARG_STRING = 128
STATUS_RECORDED = "RECORDED"
STATUS_REPLAY_RESERVED = "REPLAY_RESERVED"
STATUS_REPLAY_ENQUEUED = "REPLAY_ENQUEUED"
STATUS_PUBLISH_FAILED = "PUBLISH_FAILED"
STATUS_PUBLISH_UNCERTAIN = "PUBLISH_UNCERTAIN"
STATUS_REPLAY_LEASE_EXPIRED = "REPLAY_LEASE_EXPIRED"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_IGNORED_EXPIRED = "IGNORED_EXPIRED"
STATUS_PERMANENT_FAILED = "PERMANENT_FAILED"
STATUS_REPLAY_FAILED = "REPLAY_FAILED"

_TERMINAL = frozenset(
    {
        STATUS_SUCCEEDED,
        STATUS_IGNORED_EXPIRED,
        STATUS_PERMANENT_FAILED,
    }
)
_IN_FLIGHT = frozenset(
    {
        STATUS_REPLAY_RESERVED,
        STATUS_REPLAY_ENQUEUED,
        STATUS_PUBLISH_UNCERTAIN,
    }
)
_REPLAYABLE = frozenset(
    {
        STATUS_RECORDED,
        STATUS_PUBLISH_FAILED,
        STATUS_REPLAY_LEASE_EXPIRED,
        STATUS_REPLAY_FAILED,
    }
)
_RELEASE_LEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) else return 0 end"
)
_RENEW_LEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) else return 0 end"
)


def _key(settings: Settings) -> str:
    return f"property_ai:{settings.app_env}:failed_tasks"


def _hash_key(settings: Settings) -> str:
    return f"{_key(settings)}:records"


def _open_key(settings: Settings) -> str:
    return f"{_key(settings)}:open"


def _task_index_key(settings: Settings) -> str:
    return f"{_key(settings)}:by_task"


def _lease_key(settings: Settings, record_id: str) -> str:
    return f"{_key(settings)}:lease:{record_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_arg_item(item: Any) -> Any:
    if isinstance(item, bool) or item is None:
        return item
    if isinstance(item, int) and not isinstance(item, bool):
        return item
    if isinstance(item, str):
        lowered = item.casefold()
        if any(
            token in lowered
            for token in ("bearer ", "eyj", "secret", "password", "session_key", "api_key")
        ):
            return "[redacted]"
        if len(item) > _MAX_ARG_STRING:
            return "[truncated]"
        return item
    return "[unsupported]"


def _sanitize_args(args: tuple[Any, ...] | list[Any] | None) -> list[Any]:
    """Keep argument positions. Do not drop unknown values to shift the contract."""

    return [_sanitize_arg_item(item) for item in (args or ())]


def _sanitize_extra(extra: dict[str, Any] | None) -> dict[str, Any]:
    return redact_value(dict(extra or {}))


def validate_operation_args(operation: str, args: list[Any] | tuple[Any, ...] | None) -> tuple[Any, ...]:
    spec = _OPERATION_ARGS.get(operation)
    if spec is None:
        raise ValueError("unknown operation")
    values = list(args or ())
    if len(values) != len(spec):
        raise ValueError("argument count does not match operation contract")
    coerced: list[Any] = []
    for value, (_name, expected) in zip(values, spec, strict=True):
        if expected is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("integer argument is invalid")
            coerced.append(value)
            continue
        if expected is str:
            if not isinstance(value, str) or not value or len(value) > _MAX_ARG_STRING:
                raise ValueError("string argument is invalid")
            if value == "[redacted]" or value == "[truncated]" or value == "[unsupported]":
                raise ValueError("argument was redacted and cannot be replayed")
            coerced.append(value)
            continue
        raise ValueError("unsupported argument type")
    return tuple(coerced)


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
    if task_id:
        try:
            existing = get_failed_task(str(task_id), resolved)
        except Exception:
            existing = None
        else:
            if existing is not None:
                if int(existing.get("current_attempt") or 0) > 0:
                    complete_attempt(
                        str(task_id),
                        STATUS_REPLAY_FAILED,
                        resolved,
                        error_code=error_code,
                    )
                else:
                    existing["error_code"] = error_code
                    existing["args"] = _sanitize_args(args) or existing.get("args")
                    try:
                        _store_record(resolved, existing)
                    except Exception:
                        logger.warning("failed_task_record_update_unavailable", task_id=task_id)
                return str(existing.get("record_id") or task_id)
    record_id = task_id or f"enqueue-{uuid4().hex}"
    payload = {
        "record_id": record_id,
        "task_id": task_id,
        "operation": operation,
        "error_code": error_code,
        "args": _sanitize_args(args),
        "extra": _sanitize_extra(extra),
        "recorded_at": _now(),
        "queue": resolved.resolved_celery_queue,
        "dlq": resolved.resolved_celery_dlq,
        "status": STATUS_RECORDED,
        "replay_task_id": None,
        "replayed": False,
        "current_attempt": 0,
        "attempts": [],
        "parent_record_id": record_id,
    }
    redis_ok = False
    dlq_ok = False
    try:
        client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
        pipe = client.pipeline()
        pipe.hset(_hash_key(resolved), record_id, json.dumps(payload, ensure_ascii=False))
        pipe.zadd(_open_key(resolved), {record_id: datetime.now(timezone.utc).timestamp()})
        if task_id:
            pipe.hset(_task_index_key(resolved), task_id, record_id)
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


def list_failed_tasks(
    settings: Settings | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    resolved = settings or get_settings()
    start = max(0, offset)
    stop = start + max(0, limit) - 1
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        record_ids = client.zrevrange(_open_key(resolved), start, max(start, stop))
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


def get_failed_task(record_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """Locate one record by record_id or indexed Celery task_id. Does not scan lists."""

    resolved = settings or get_settings()
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        raw = client.hget(_hash_key(resolved), record_id)
        if not raw:
            mapped = client.hget(_task_index_key(resolved), record_id)
            if mapped:
                raw = client.hget(_hash_key(resolved), mapped)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    finally:
        client.close()


def complete_attempt(
    celery_task_id: str,
    outcome: str,
    settings: Settings | None = None,
    *,
    error_code: str | None = None,
) -> bool:
    """Atomically record a business outcome for the attempt that owns celery_task_id."""

    if outcome not in {
        STATUS_SUCCEEDED,
        STATUS_IGNORED_EXPIRED,
        STATUS_PERMANENT_FAILED,
        STATUS_REPLAY_FAILED,
    }:
        raise ValueError("unsupported attempt outcome")
    resolved = settings or get_settings()
    record = get_failed_task(celery_task_id, resolved)
    if record is None:
        return False
    expected_attempt = int(record.get("current_attempt") or 0)
    replay_id = str(record.get("replay_task_id") or "")
    original_id = str(record.get("task_id") or "")
    if expected_attempt <= 0:
        allowed_ids = {original_id, str(record.get("record_id") or "")}
    else:
        allowed_ids = {replay_id}
    if celery_task_id not in allowed_ids:
        logger.warning(
            "failed_task_outcome_stale_callback",
            record_id=record.get("record_id"),
            celery_task_id=celery_task_id,
            current_attempt=expected_attempt,
        )
        return False
    if record.get("status") in _TERMINAL and record.get("status") != outcome:
        logger.warning(
            "failed_task_outcome_ignored_terminal",
            record_id=record.get("record_id"),
            status=record.get("status"),
            ignored_outcome=outcome,
        )
        return False

    def mutate(current: dict[str, Any]) -> None:
        current["status"] = outcome
        current["error_code"] = error_code or current.get("error_code")
        current["completed_at"] = _now()
        attempts = list(current.get("attempts") or [])
        attempts.append(
            {
                "attempt": current.get("current_attempt"),
                "task_id": celery_task_id,
                "status": outcome,
                "at": _now(),
                "error_code": error_code,
            }
        )
        current["attempts"] = attempts[-20:]
        if outcome == STATUS_SUCCEEDED:
            current["replayed"] = True

    return _cas_record(
        resolved,
        str(record["record_id"]),
        expected_attempt=expected_attempt,
        expected_statuses=None,
        mutator=mutate,
        allow_terminal_overwrite=False,
    )


def replay_failed_task(record_id: str, settings: Settings | None = None) -> bool:
    """Reserve attempt/task_id, then enqueue. Does not mark business success."""

    resolved = settings or get_settings()
    match = get_failed_task(record_id, resolved)
    if match is None:
        return False
    operation = str(match.get("operation") or "")
    if operation not in _ALLOWED_OPERATIONS:
        logger.error("failed_task_replay_unknown_operation", operation=operation)
        return False
    if match.get("queue") not in {None, resolved.resolved_celery_queue}:
        logger.error("failed_task_replay_queue_mismatch", operation=operation)
        return False
    status = str(match.get("status") or STATUS_RECORDED)
    if status in _TERMINAL:
        logger.warning("failed_task_replay_already_terminal", record_id=match["record_id"], status=status)
        return False
    if status not in _REPLAYABLE | _IN_FLIGHT:
        logger.warning("failed_task_replay_not_replayable", record_id=match["record_id"], status=status)
        return False
    try:
        args = validate_operation_args(operation, match.get("args") or [])
    except ValueError as exc:
        logger.error("failed_task_replay_invalid_args", record_id=match["record_id"], reason=str(exc))
        return False

    expected_attempt = int(match.get("current_attempt") or 0)
    reserved_task_id = f"replay-{uuid4().hex}"
    client = redis.Redis.from_url(resolved.redis_url, socket_timeout=2.0)
    try:
        leased = client.set(
            _lease_key(resolved, str(match["record_id"])),
            reserved_task_id,
            nx=True,
            ex=_LEASE_SECONDS,
        )
        if not leased:
            logger.warning("failed_task_replay_in_progress", record_id=match["record_id"])
            return False
        if status in _IN_FLIGHT:
            expired = _cas_record(
                resolved,
                str(match["record_id"]),
                expected_attempt=expected_attempt,
                expected_statuses=_IN_FLIGHT,
                mutator=lambda current: current.update(
                    {"status": STATUS_REPLAY_LEASE_EXPIRED}
                ),
            )
            _release_lease(resolved, str(match["record_id"]), reserved_task_id)
            logger.warning(
                "failed_task_replay_lease_expired",
                record_id=match["record_id"],
                expired=expired,
            )
            return False
    finally:
        client.close()

    next_attempt = expected_attempt + 1

    reserved = _cas_record(
        resolved,
        str(match["record_id"]),
        expected_attempt=expected_attempt,
        expected_statuses=_REPLAYABLE,
        mutator=lambda current: current.update(
            {
                "status": STATUS_REPLAY_RESERVED,
                "current_attempt": next_attempt,
                "replay_task_id": reserved_task_id,
                "replayed": False,
            }
        ),
        index_task_id=reserved_task_id,
    )
    if not reserved:
        _release_lease(resolved, str(match["record_id"]), reserved_task_id)
        return False
    _renew_lease(resolved, str(match["record_id"]), reserved_task_id, _LEASE_SECONDS)

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
    try:
        async_result = mapping[operation].apply_async(
            args=args,
            queue=resolved.resolved_celery_queue,
            task_id=reserved_task_id,
        )
    except (TypeError, ValueError):
        _cas_record(
            resolved,
            str(match["record_id"]),
            expected_attempt=next_attempt,
            expected_statuses={STATUS_REPLAY_RESERVED},
            mutator=lambda current: current.update({"status": STATUS_PUBLISH_FAILED}),
        )
        _release_lease(resolved, str(match["record_id"]), reserved_task_id)
        logger.error("failed_task_replay_publish_failed", record_id=match["record_id"])
        return False
    except Exception:
        _cas_record(
            resolved,
            str(match["record_id"]),
            expected_attempt=next_attempt,
            expected_statuses={STATUS_REPLAY_RESERVED},
            mutator=lambda current: current.update({"status": STATUS_PUBLISH_UNCERTAIN}),
        )
        logger.warning(
            "failed_task_replay_publish_uncertain",
            record_id=match["record_id"],
        )
        return False

    published_id = str(getattr(async_result, "id", reserved_task_id) or reserved_task_id)
    enqueued = _cas_record(
        resolved,
        str(match["record_id"]),
        expected_attempt=next_attempt,
        expected_statuses={STATUS_REPLAY_RESERVED},
        mutator=lambda current: current.update(
            {
                "status": STATUS_REPLAY_ENQUEUED,
                "replay_task_id": published_id,
            }
        ),
        index_task_id=published_id,
    )
    if not enqueued:
        current = get_failed_task(str(match["record_id"]), resolved)
        if current is not None and current.get("status") in _TERMINAL:
            logger.info(
                "failed_task_replay_completed_before_enqueue_writeback",
                record_id=match["record_id"],
                status=current.get("status"),
            )
            return True
        _cas_record(
            resolved,
            str(match["record_id"]),
            expected_attempt=next_attempt,
            expected_statuses={STATUS_REPLAY_RESERVED, STATUS_REPLAY_ENQUEUED},
            mutator=lambda payload: payload.update({"status": STATUS_PUBLISH_UNCERTAIN}),
            allow_terminal_overwrite=False,
        )
        logger.warning("failed_task_replay_publish_uncertain", record_id=match["record_id"])
        return False
    logger.info(
        "failed_task_replay_enqueued",
        record_id=match["record_id"],
        operation=operation,
        replay_task_id=published_id,
        attempt=next_attempt,
    )
    return True


def _cas_record(
    settings: Settings,
    record_id: str,
    *,
    expected_attempt: int,
    expected_statuses: set[str] | None,
    mutator: Any,
    index_task_id: str | None = None,
    allow_terminal_overwrite: bool = False,
) -> bool:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    hash_key = _hash_key(settings)
    try:
        for _attempt in range(_CAS_RETRIES):
            pipe = None
            try:
                pipe = client.pipeline()
                pipe.watch(hash_key)
                raw = client.hget(hash_key, record_id)
                if not raw:
                    pipe.reset()
                    return False
                try:
                    current = json.loads(raw)
                except json.JSONDecodeError:
                    pipe.reset()
                    return False
                if int(current.get("current_attempt") or 0) != expected_attempt:
                    pipe.reset()
                    return False
                status = str(current.get("status") or "")
                if not allow_terminal_overwrite and status in _TERMINAL:
                    pipe.reset()
                    return False
                if expected_statuses is not None and status not in expected_statuses:
                    pipe.reset()
                    return False
                mutator(current)
                if (
                    current.get("status") in _TERMINAL
                    and status in _TERMINAL
                    and current.get("status") != status
                ):
                    pipe.reset()
                    return False
                pipe.multi()
                pipe.hset(hash_key, record_id, json.dumps(current, ensure_ascii=False))
                if current.get("status") in _TERMINAL:
                    pipe.zrem(_open_key(settings), record_id)
                else:
                    pipe.zadd(
                        _open_key(settings),
                        {record_id: datetime.now(timezone.utc).timestamp()},
                    )
                if index_task_id:
                    pipe.hset(_task_index_key(settings), index_task_id, record_id)
                pipe.execute()
                return True
            except redis.WatchError:
                logger.warning(
                    "failed_task_cas_contention",
                    record_id=record_id,
                    attempt=_attempt + 1,
                )
                continue
            finally:
                if pipe is not None:
                    try:
                        pipe.reset()
                    except Exception:
                        pass
        return False
    finally:
        client.close()


def _release_lease(settings: Settings, record_id: str, token: str) -> bool:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        released = client.eval(
            _RELEASE_LEASE_LUA,
            1,
            _lease_key(settings, record_id),
            token,
        )
        return int(released or 0) == 1
    finally:
        client.close()


def _renew_lease(
    settings: Settings,
    record_id: str,
    token: str,
    seconds: int,
) -> bool:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        renewed = client.eval(
            _RENEW_LEASE_LUA,
            1,
            _lease_key(settings, record_id),
            token,
            int(seconds),
        )
        return int(renewed or 0) == 1
    finally:
        client.close()


def _store_record(settings: Settings, payload: dict[str, Any]) -> None:
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        client.hset(
            _hash_key(settings),
            str(payload["record_id"]),
            json.dumps(payload, ensure_ascii=False),
        )
        if payload.get("status") in _TERMINAL:
            client.zrem(_open_key(settings), payload["record_id"])
        if payload.get("replay_task_id"):
            client.hset(
                _task_index_key(settings),
                str(payload["replay_task_id"]),
                str(payload["record_id"]),
            )
        if payload.get("task_id"):
            client.hset(
                _task_index_key(settings),
                str(payload["task_id"]),
                str(payload["record_id"]),
            )
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
