"""Real Redis lease compare-and-delete, expiry, CAS, and replay outcomes."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import redis

from app.core.config import Settings
from app.core.errors import AiErrorCode
from app.worker.failed_tasks import (
    STATUS_PERMANENT_FAILED,
    STATUS_REPLAY_ENQUEUED,
    STATUS_REPLAY_RESERVED,
    STATUS_SUCCEEDED,
    _cas_record,
    _lease_key,
    _release_lease,
    _renew_lease,
    complete_attempt,
    get_failed_task,
    record_failed_task,
    replay_failed_task,
)
from app.worker.tasks import (
    _record_analysis_attempt_outcome,
    _record_delivery_attempt_outcome,
    _record_ingest_attempt_outcome,
)


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", wechat_login_enabled=False)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


@pytest.fixture
def lease_namespace(monkeypatch):
    settings = _settings()
    namespace = uuid4().hex
    monkeypatch.setattr(
        "app.worker.failed_tasks._key",
        lambda _settings, _ns=namespace: f"property_ai:test:failed_tasks:{_ns}",
    )
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        assert client.ping() is True
    except Exception as exc:
        client.close()
        raise RuntimeError("real Redis is required for lease tests") from exc
    try:
        yield settings, namespace
    finally:
        prefix = f"property_ai:test:failed_tasks:{namespace}"
        try:
            for key in client.scan_iter(match=f"{prefix}*", count=100):
                client.delete(key)
        finally:
            client.close()


def test_stale_holder_cannot_delete_new_lease(lease_namespace) -> None:
    settings, _namespace = lease_namespace
    record_failed_task(
        settings,
        task_id="lease-old",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(11, "rid-11"),
    )
    holder = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    rival = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    key = _lease_key(settings, "lease-old")
    try:
        assert holder.set(key, "old-token", nx=True, ex=30)
        rival.set(key, "new-token", ex=30)
        assert _release_lease(settings, "lease-old", "old-token") is False
        assert _text(holder.get(key)) == "new-token"
        assert _renew_lease(settings, "lease-old", "old-token", 30) is False
        assert _renew_lease(settings, "lease-old", "new-token", 30) is True
    finally:
        holder.close()
        rival.close()


def test_expired_lock_new_holder_is_not_overwritten(lease_namespace) -> None:
    settings, _namespace = lease_namespace
    record_failed_task(
        settings,
        task_id="lease-exp",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(12, "rid-12"),
    )
    from app.worker.failed_tasks import _store_record

    record = get_failed_task("lease-exp", settings)
    assert record is not None
    record["status"] = STATUS_REPLAY_ENQUEUED
    record["current_attempt"] = 1
    record["replay_task_id"] = "replay-live"
    _store_record(settings, record)
    client = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    other = redis.Redis.from_url(settings.redis_url, socket_timeout=2.0)
    key = _lease_key(settings, "lease-exp")
    try:
        assert client.set(key, "old-token", nx=True, ex=1)
        import time

        deadline = time.time() + 3
        while time.time() < deadline and client.get(key) is not None:
            time.sleep(0.1)
        assert other.set(key, "new-token", nx=True, ex=30)
        assert complete_attempt("replay-live", STATUS_SUCCEEDED, settings) is True
        assert get_failed_task("lease-exp", settings)["status"] == STATUS_SUCCEEDED
        assert _release_lease(settings, "lease-exp", "old-token") is False
        assert _text(other.get(key)) == "new-token"
        assert complete_attempt("replay-stale", STATUS_PERMANENT_FAILED, settings) is False
        assert get_failed_task("lease-exp", settings)["status"] == STATUS_SUCCEEDED
    finally:
        client.close()
        other.close()


def test_fast_complete_is_not_reverted_to_enqueued(lease_namespace) -> None:
    settings, _namespace = lease_namespace
    record_failed_task(
        settings,
        task_id="fast-done",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(13, "rid-13"),
    )
    from app.worker.failed_tasks import _store_record

    record = get_failed_task("fast-done", settings)
    assert record is not None
    record["status"] = STATUS_REPLAY_RESERVED
    record["current_attempt"] = 1
    record["replay_task_id"] = "replay-fast"
    _store_record(settings, record)
    assert complete_attempt("replay-fast", STATUS_SUCCEEDED, settings) is True
    wrote = _cas_record(
        settings,
        "fast-done",
        expected_attempt=1,
        expected_statuses={STATUS_REPLAY_RESERVED},
        mutator=lambda current: current.update({"status": STATUS_REPLAY_ENQUEUED}),
    )
    assert wrote is False
    assert get_failed_task("fast-done", settings)["status"] == STATUS_SUCCEEDED


def test_cas_contention_is_observable(lease_namespace) -> None:
    settings, _namespace = lease_namespace
    record_failed_task(
        settings,
        task_id="cas-race",
        operation="app.worker.tasks.deliver_notification_task",
        error_code="NTF.PROVIDER_FAILED",
        args=(44,),
    )

    def bump(label: str) -> bool:
        return _cas_record(
            settings,
            "cas-race",
            expected_attempt=0,
            expected_statuses=None,
            mutator=lambda current: current.update({"error_code": label}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bump, "worker-a")
        second = pool.submit(bump, "worker-b")
        results = {first.result(timeout=10), second.result(timeout=10)}
    assert True in results
    stored = get_failed_task("cas-race", settings)
    assert stored is not None
    assert stored["error_code"] in {"worker-a", "worker-b"}


def test_three_operations_record_business_not_celery_success(lease_namespace) -> None:
    settings, _namespace = lease_namespace
    analyze_id = record_failed_task(
        settings,
        task_id="op-analyze",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(21, "rid-21"),
    )
    ingest_id = record_failed_task(
        settings,
        task_id="op-ingest",
        operation="app.worker.tasks.ingest_knowledge_task",
        error_code="KB.UNAVAILABLE",
        args=(31, 1, "rid-31"),
    )
    deliver_id = record_failed_task(
        settings,
        task_id="op-deliver",
        operation="app.worker.tasks.deliver_notification_task",
        error_code="NTF.PROVIDER_FAILED",
        args=(41,),
    )
    from app.worker.failed_tasks import _store_record

    for record_id, replay_id in (
        (analyze_id, "replay-analyze"),
        (ingest_id, "replay-ingest"),
        (deliver_id, "replay-deliver"),
    ):
        record = get_failed_task(str(record_id), settings)
        assert record is not None
        record["status"] = STATUS_REPLAY_ENQUEUED
        record["current_attempt"] = 1
        record["replay_task_id"] = replay_id
        _store_record(settings, record)

    class Completed:
        status = "COMPLETED"
        error_code = None

    _record_analysis_attempt_outcome("replay-analyze", Completed())
    _record_ingest_attempt_outcome("replay-ingest", "SUCCEEDED")
    _record_delivery_attempt_outcome("replay-deliver", "FAILED")
    assert get_failed_task("op-analyze", settings)["status"] == STATUS_SUCCEEDED
    assert get_failed_task("op-ingest", settings)["status"] == STATUS_SUCCEEDED
    assert get_failed_task("op-deliver", settings)["status"] == STATUS_PERMANENT_FAILED

    class Invalid:
        status = "FAILED"
        error_code = AiErrorCode.INVALID_OUTPUT

    _record_analysis_attempt_outcome("replay-analyze", Invalid())
    assert get_failed_task("op-analyze", settings)["status"] == STATUS_SUCCEEDED


def test_replay_duplicate_callback_and_unknown_task_are_ignored(
    lease_namespace,
    monkeypatch,
) -> None:
    settings, _namespace = lease_namespace
    record_failed_task(
        settings,
        task_id="dup-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(15, "rid-15"),
    )
    from unittest.mock import MagicMock

    task = MagicMock()
    task.apply_async.return_value = MagicMock(id="replay-dup")
    monkeypatch.setattr("app.worker.tasks.analyze_work_order_task", task)

    class FakeUUID:
        hex = "dup"

    monkeypatch.setattr("app.worker.failed_tasks.uuid4", lambda: FakeUUID())
    assert replay_failed_task("dup-1", settings) is True
    assert replay_failed_task("dup-1", settings) is False
    assert complete_attempt("replay-dup", STATUS_SUCCEEDED, settings) is True
    assert complete_attempt("replay-dup", STATUS_PERMANENT_FAILED, settings) is False
    assert complete_attempt("missing-task", STATUS_SUCCEEDED, settings) is False
    assert get_failed_task("dup-1", settings)["status"] == STATUS_SUCCEEDED
