"""Failed-task Redis records: direct lookup, arg contracts, attempt CAS, outcomes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.errors import AiErrorCode
from app.worker.celery_app import _log_permanent_or_retry_failure, _note_celery_success_is_not_business_success
from app.worker.failed_tasks import (
    STATUS_PERMANENT_FAILED,
    STATUS_REPLAY_ENQUEUED,
    STATUS_REPLAY_FAILED,
    STATUS_REPLAY_LEASE_EXPIRED,
    STATUS_RECORDED,
    STATUS_SUCCEEDED,
    complete_attempt,
    get_failed_task,
    record_failed_task,
    replay_failed_task,
    validate_operation_args,
)
from app.worker.tasks import _record_analysis_attempt_outcome


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", wechat_login_enabled=False)


class MemoryRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.kv: dict[str, str] = {}
        self._in_multi = False

    def pipeline(self):
        return self

    def watch(self, *_args, **_kwargs):
        return True

    def reset(self) -> None:
        self._in_multi = False

    def multi(self) -> None:
        self._in_multi = True

    def execute(self) -> list[object]:
        self._in_multi = False
        return []

    def hset(self, key: str, field: str, value: str):
        self.hashes.setdefault(key, {})[str(field)] = value
        return self

    def hget(self, key: str, field: str):
        return self.hashes.get(key, {}).get(str(field))

    def zadd(self, key: str, mapping: dict[str, float]):
        self.zsets.setdefault(key, {}).update(mapping)
        return self

    def zrem(self, key: str, member: str):
        self.zsets.get(key, {}).pop(member, None)
        return self

    def zrevrange(self, key: str, start: int, stop: int):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1], reverse=True)
        end = None if stop < 0 else stop + 1
        return [item[0] for item in items[start:end]]

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        del ex
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key: str):
        return self.kv.get(key)

    def delete(self, key: str):
        self.kv.pop(key, None)

    def close(self) -> None:
        return None


def _bind_redis(monkeypatch, memory: MemoryRedis | None = None) -> MemoryRedis:
    client = memory or MemoryRedis()
    monkeypatch.setattr(
        "app.worker.failed_tasks.redis.Redis.from_url",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr("app.worker.failed_tasks._publish_dlq", lambda *_a, **_k: None)
    return client


def test_record_failed_task_still_publishes_dlq_when_redis_is_down(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.worker.failed_tasks.redis.Redis.from_url",
        MagicMock(side_effect=RuntimeError("redis down")),
    )
    monkeypatch.setattr("app.worker.failed_tasks._publish_dlq", lambda *_args, **_kwargs: None)
    record_id = record_failed_task(
        _settings(),
        task_id="t-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        extra={"authorization": "Bearer secret-token", "password": "plain"},
        args=(1, "rid"),
    )
    assert record_id == "t-1"


def test_record_failed_task_returns_none_when_redis_and_dlq_are_down(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.worker.failed_tasks.redis.Redis.from_url",
        MagicMock(side_effect=RuntimeError("redis down")),
    )
    monkeypatch.setattr(
        "app.worker.failed_tasks._publish_dlq",
        MagicMock(side_effect=RuntimeError("broker down")),
    )
    assert (
        record_failed_task(
            _settings(),
            task_id="t-2",
            operation="app.worker.tasks.analyze_work_order_task",
            error_code="ENQUEUE_FAILED",
            args=(1, "rid"),
        )
        is None
    )


def test_record_payload_redacts_secrets_without_dropping_positions(monkeypatch) -> None:
    client = _bind_redis(monkeypatch)
    record_failed_task(
        _settings(),
        task_id="t-secret",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        extra={"authorization": "Bearer abc", "session_key": "wx-sess"},
        args=("Bearer eyJaaaaaaaaaa.bbbbbbbbbb.cccccccccc", 9),
    )
    stored = json.loads(client.hget("property_ai:test:failed_tasks:records", "t-secret"))
    assert stored["args"] == ["[redacted]", 9]
    assert "Bearer abc" not in str(stored)
    assert "wx-sess" not in str(stored)


def test_get_failed_task_is_direct_hget_not_list_scan(monkeypatch) -> None:
    client = _bind_redis(monkeypatch)
    record_failed_task(
        _settings(),
        task_id="direct-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(12, "rid-12"),
    )
    found = get_failed_task("direct-1", _settings())
    assert found is not None
    assert found["record_id"] == "direct-1"
    assert found["args"] == [12, "rid-12"]
    assert any("records" in key for key in client.hashes)


def test_validate_operation_args_rejects_shifted_or_unknown_values() -> None:
    with pytest.raises(ValueError):
        validate_operation_args(
            "app.worker.tasks.analyze_work_order_task",
            [True, "rid"],
        )
    with pytest.raises(ValueError):
        validate_operation_args(
            "app.worker.tasks.analyze_work_order_task",
            [1],
        )
    with pytest.raises(ValueError):
        validate_operation_args(
            "app.worker.tasks.analyze_work_order_task",
            [1, "rid", "extra"],
        )
    assert validate_operation_args(
        "app.worker.tasks.analyze_work_order_task",
        [9, "rid-9"],
    ) == (9, "rid-9")


def test_open_records_are_not_ltrimmed() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "app" / "worker" / "failed_tasks.py"
    text = source.read_text(encoding="utf-8")
    assert "ltrim" not in text
    assert "_open_key" in text
    assert "zadd" in text
    assert "limit=500" not in text


def test_replay_unknown_operation_is_false(monkeypatch) -> None:
    client = _bind_redis(monkeypatch)
    client.hset(
        "property_ai:test:failed_tasks:records",
        "x",
        json.dumps({"record_id": "x", "operation": "os.system", "args": [], "status": STATUS_RECORDED}),
    )
    assert replay_failed_task("x", _settings()) is False


def test_replay_analyze_task_reserves_then_marks_enqueued_not_success(monkeypatch) -> None:
    settings = _settings()
    client = _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="wo-9",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(9, "rid-9"),
    )
    task = MagicMock()
    task.apply_async.return_value = MagicMock(id="replay-fixed")
    monkeypatch.setattr("app.worker.tasks.analyze_work_order_task", task)
    class FakeUUID:
        hex = "fixed"

    monkeypatch.setattr("app.worker.failed_tasks.uuid4", lambda: FakeUUID())
    assert replay_failed_task("wo-9", settings) is True
    task.apply_async.assert_called_once()
    kwargs = task.apply_async.call_args.kwargs
    assert kwargs["args"] == (9, "rid-9")
    assert kwargs["queue"] == "property_ai.test"
    stored = get_failed_task("wo-9", settings)
    assert stored is not None
    assert stored["status"] == STATUS_REPLAY_ENQUEUED
    assert stored["replay_task_id"]
    assert stored["replayed"] is False
    assert stored["current_attempt"] == 1


def test_replay_lease_rejects_concurrent_attempt(monkeypatch) -> None:
    settings = _settings()
    client = _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="wo-9",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(9, "rid-9"),
    )
    client.set("property_ai:test:failed_tasks:lease:wo-9", "held")
    assert replay_failed_task("wo-9", settings) is False


def test_replay_does_not_revert_succeeded_to_enqueued(monkeypatch) -> None:
    settings = _settings()
    _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="done-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(3, "rid-3"),
    )
    record = get_failed_task("done-1", settings)
    assert record is not None
    record["status"] = STATUS_SUCCEEDED
    record["current_attempt"] = 1
    record["replay_task_id"] = "replay-old"
    from app.worker.failed_tasks import _store_record

    _store_record(settings, record)
    assert replay_failed_task("done-1", settings) is False
    assert get_failed_task("done-1", settings)["status"] == STATUS_SUCCEEDED


def test_stale_callback_cannot_overwrite_new_attempt(monkeypatch) -> None:
    settings = _settings()
    _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="orig-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(4, "rid-4"),
    )
    record = get_failed_task("orig-1", settings)
    assert record is not None
    record["status"] = STATUS_REPLAY_ENQUEUED
    record["current_attempt"] = 2
    record["replay_task_id"] = "replay-new"
    from app.worker.failed_tasks import _store_record

    _store_record(settings, record)
    assert complete_attempt("orig-1", STATUS_SUCCEEDED, settings) is False
    assert get_failed_task("orig-1", settings)["status"] == STATUS_REPLAY_ENQUEUED
    assert complete_attempt("replay-new", STATUS_PERMANENT_FAILED, settings) is True
    assert get_failed_task("orig-1", settings)["status"] == STATUS_PERMANENT_FAILED


def test_expired_in_flight_is_marked_lease_expired_not_replayed(monkeypatch) -> None:
    settings = _settings()
    _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="inflight-1",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(5, "rid-5"),
    )
    record = get_failed_task("inflight-1", settings)
    assert record is not None
    record["status"] = STATUS_REPLAY_ENQUEUED
    record["current_attempt"] = 1
    record["replay_task_id"] = "replay-live"
    from app.worker.failed_tasks import _store_record

    _store_record(settings, record)
    assert replay_failed_task("inflight-1", settings) is False
    assert get_failed_task("inflight-1", settings)["status"] == STATUS_REPLAY_LEASE_EXPIRED


def test_replay_re_fail_updates_original_record(monkeypatch) -> None:
    settings = _settings()
    _bind_redis(monkeypatch)
    record_failed_task(
        settings,
        task_id="orig-fail",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        args=(6, "rid-6"),
    )
    record = get_failed_task("orig-fail", settings)
    assert record is not None
    record["status"] = STATUS_REPLAY_ENQUEUED
    record["current_attempt"] = 1
    record["replay_task_id"] = "replay-2"
    from app.worker.failed_tasks import _store_record

    _store_record(settings, record)
    linked = record_failed_task(
        settings,
        task_id="replay-2",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.UNAVAILABLE",
        args=(6, "rid-6"),
    )
    assert linked == "orig-fail"
    updated = get_failed_task("orig-fail", settings)
    assert updated is not None
    assert updated["status"] == STATUS_REPLAY_FAILED
    assert updated["error_code"] == "AI.UNAVAILABLE"


def test_celery_success_signal_does_not_mark_business_success() -> None:
    _note_celery_success_is_not_business_success(sender=MagicMock())


def test_invalid_output_is_permanent_failure_not_succeeded(monkeypatch) -> None:
    class Analysis:
        status = "FAILED"
        error_code = AiErrorCode.INVALID_OUTPUT

    calls: list[tuple[str, str]] = []

    def fake_complete(task_id: str, outcome: str, settings=None, error_code=None):
        del settings, error_code
        calls.append((task_id, outcome))
        return True

    monkeypatch.setattr("app.worker.failed_tasks.complete_attempt", fake_complete)
    _record_analysis_attempt_outcome("t-invalid", Analysis())
    assert calls == [("t-invalid", STATUS_PERMANENT_FAILED)]


def test_first_non_retryable_celery_failure_is_recorded(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fake_record(settings, **kwargs):
        recorded.update(kwargs)
        return kwargs.get("task_id")

    monkeypatch.setattr("app.worker.failed_tasks.record_failed_task", fake_record)
    sender = MagicMock()
    sender.max_retries = 2
    sender.request.retries = 0
    sender.request.args = (1, "rid")
    sender.name = "app.worker.tasks.analyze_work_order_task"
    _log_permanent_or_retry_failure(
        sender=sender,
        task_id="first-fail",
        exception=ValueError("provider down"),
    )
    assert recorded["task_id"] == "first-fail"
    assert recorded["error_code"] == "ValueError"
