"""Failed-task Redis records and replay mapping."""

from unittest.mock import MagicMock

from app.core.config import Settings
from app.worker.failed_tasks import record_failed_task, replay_failed_task


def test_record_failed_task_survives_redis_outage(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.worker.failed_tasks.redis.Redis.from_url",
        MagicMock(side_effect=RuntimeError("redis down")),
    )
    settings = Settings(_env_file=None, app_env="test", wechat_login_enabled=False)
    assert (
        record_failed_task(
            settings,
            task_id="t-1",
            operation="app.worker.tasks.analyze_work_order_task",
            error_code="AI.TIMEOUT",
            args=(1, "rid"),
        )
        is None
    )


def test_replay_unknown_operation_is_false(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.worker.failed_tasks.list_failed_tasks",
        lambda settings, limit=50: [
            {"record_id": "x", "operation": "not.a.task", "args": []},
        ],
    )
    settings = Settings(_env_file=None, app_env="test", wechat_login_enabled=False)
    assert replay_failed_task("x", settings) is False


def test_replay_analyze_task_requeues(monkeypatch) -> None:
    settings = Settings(_env_file=None, app_env="test", wechat_login_enabled=False)
    task = MagicMock()
    monkeypatch.setattr(
        "app.worker.failed_tasks.list_failed_tasks",
        lambda _settings, limit=50: [
            {
                "record_id": "wo-9",
                "operation": "app.worker.tasks.analyze_work_order_task",
                "args": [9, "rid-9"],
            }
        ],
    )
    monkeypatch.setattr("app.worker.tasks.analyze_work_order_task", task)
    assert replay_failed_task("wo-9", settings) is True
    task.apply_async.assert_called_once_with(args=(9, "rid-9"), queue="property_ai.test")
