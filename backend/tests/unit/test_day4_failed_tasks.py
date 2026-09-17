"""Failed-task Redis/DLQ recording, lease replay, and Celery first-failure tracking."""

from unittest.mock import MagicMock

from app.core.config import Settings
from app.worker.celery_app import _log_permanent_or_retry_failure
from app.worker.failed_tasks import (
    STATUS_REPLAY_ENQUEUED,
    STATUS_RECORDED,
    record_failed_task,
    replay_failed_task,
)


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", wechat_login_enabled=False)


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
            args=(1,),
        )
        is None
    )


def test_record_payload_redacts_secrets(monkeypatch) -> None:
    stored: dict[str, object] = {}

    class FakeRedis:
        def pipeline(self):
            return self

        def hset(self, *_args, **_kwargs):
            stored["payload"] = _args[2] if len(_args) > 2 else _kwargs.get("value")
            return self

        def zadd(self, *_args, **_kwargs):
            return self

        def execute(self):
            return []

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "app.worker.failed_tasks.redis.Redis.from_url",
        lambda *_a, **_k: FakeRedis(),
    )
    monkeypatch.setattr("app.worker.failed_tasks._publish_dlq", lambda *_a, **_k: None)
    record_failed_task(
        _settings(),
        task_id="t-secret",
        operation="app.worker.tasks.analyze_work_order_task",
        error_code="AI.TIMEOUT",
        extra={"authorization": "Bearer abc", "session_key": "wx-sess"},
        args=("Bearer eyJaaaaaaaaaa.bbbbbbbbbb.cccccccccc", 9),
    )
    payload = stored["payload"]
    assert "Bearer abc" not in str(payload)
    assert "wx-sess" not in str(payload)
    assert "eyJ" not in str(payload)


def test_open_records_are_not_ltrimmed() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "app" / "worker" / "failed_tasks.py"
    text = source.read_text(encoding="utf-8")
    assert "ltrim" not in text
    assert "_open_key" in text
    assert "zadd" in text


def test_replay_unknown_operation_is_false(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.worker.failed_tasks.list_failed_tasks",
        lambda settings, limit=50: [
            {"record_id": "x", "operation": "os.system", "args": []},
        ],
    )
    assert replay_failed_task("x", _settings()) is False


def test_replay_analyze_task_marks_enqueued_not_success(monkeypatch) -> None:
    settings = _settings()
    task = MagicMock()
    task.apply_async.return_value = MagicMock(id="replay-99")
    stored: dict[str, object] = {}
    client = MagicMock()
    client.set.return_value = True
    monkeypatch.setattr(
        "app.worker.failed_tasks.list_failed_tasks",
        lambda _settings, limit=50: [
            {
                "record_id": "wo-9",
                "operation": "app.worker.tasks.analyze_work_order_task",
                "args": [9, "rid-9"],
                "status": STATUS_RECORDED,
                "queue": "property_ai.test",
            }
        ],
    )
    monkeypatch.setattr("app.worker.failed_tasks.redis.Redis.from_url", lambda *_a, **_k: client)
    monkeypatch.setattr(
        "app.worker.failed_tasks._store_record",
        lambda _settings, payload: stored.update(payload),
    )
    monkeypatch.setattr("app.worker.tasks.analyze_work_order_task", task)
    assert replay_failed_task("wo-9", settings) is True
    task.apply_async.assert_called_once_with(args=(9, "rid-9"), queue="property_ai.test")
    assert stored["status"] == STATUS_REPLAY_ENQUEUED
    assert stored["replay_task_id"] == "replay-99"
    assert stored["replayed"] is False


def test_replay_lease_rejects_concurrent_attempt(monkeypatch) -> None:
    client = MagicMock()
    client.set.return_value = False
    monkeypatch.setattr(
        "app.worker.failed_tasks.list_failed_tasks",
        lambda _settings, limit=50: [
            {
                "record_id": "wo-9",
                "operation": "app.worker.tasks.analyze_work_order_task",
                "args": [9],
                "queue": "property_ai.test",
            }
        ],
    )
    monkeypatch.setattr("app.worker.failed_tasks.redis.Redis.from_url", lambda *_a, **_k: client)
    assert replay_failed_task("wo-9", _settings()) is False


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
