"""PA-104: the durable task chain defaults on, and either mode still creates the order.

Two things are pinned here:

1. `durable_tasks_enabled` now defaults to true, so a work order commits together
   with its `ANALYZE_WORK_ORDER` task intent and the publisher owns the hand-off.
   The rollback switch (`DURABLE_TASKS_ENABLED=false`) must keep working, and a
   production assembly that never sets the flag must no longer refuse to start.
2. Neither mode may drop the order: durable mode persists the intent and leaves
   the broker alone, legacy mode hands the id to Celery (and in non-production a
   failed hand-off degrades to a recorded/logged failure instead of losing it).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.service.work_order import WorkOrderService

ANALYZE_TASK_NAME = "app.worker.tasks.analyze_work_order_task"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class _Record:
    id = 42
    status = "PENDING_ANALYSIS"


class _FakeSession:
    def add(self, _obj):  # pragma: no cover - guard, the probe must not need it
        raise AssertionError("unexpected session.add")


def _service(settings: Settings) -> WorkOrderService:
    service = object.__new__(WorkOrderService)
    service._settings = settings
    service._session = _FakeSession()
    return service


def test_default_settings_enable_the_durable_chain() -> None:
    """默认开启：不传任何覆盖时走 durable 路径。"""
    assert _settings().durable_tasks_enabled is True


def test_durable_mode_persists_the_intent_inside_the_business_transaction() -> None:
    """开模式：建单与 intent 同一个事务落库（工单不断、任务不丢）。"""
    registered: list[tuple[str, str]] = []
    with patch("app.core.database.run_owned_transaction", lambda _s, op: op()), patch(
        "app.service.task_intent.register_intent",
        lambda _session, *, operation, resource, request_id: registered.append(
            (operation, request_id)
        ),
    ):
        service = _service(_settings())
        result = service._execute(lambda: (_Record(), True), analysis_request_id="req-104-1")

    assert result[0].id == 42  # 工单仍在
    assert registered == [("ANALYZE_WORK_ORDER", "req-104-1")]  # 任务不丢


def test_durable_mode_leaves_the_broker_to_the_publisher() -> None:
    """开模式：提交点不再直投 broker（publisher 是唯一派发者）。"""
    with patch("app.worker.enqueue.enqueue_celery_task") as broker:
        _service(_settings())._enqueue_analysis(42, "req-104-1", version=3)
    broker.assert_not_called()


def test_legacy_mode_hands_the_order_to_celery() -> None:
    """关模式：不写 intent，改由提交点直投 Celery，工单照常创建。"""
    registered: list[str] = []
    queued: list[tuple[tuple, dict]] = []

    def fake_enqueue(**kwargs):
        queued.append((kwargs["args"], kwargs.get("headers") or {}))
        return True

    with patch(
        "app.service.task_intent.register_intent",
        lambda *a, **kw: registered.append("called"),
    ), patch("app.worker.enqueue.enqueue_celery_task", fake_enqueue):
        service = _service(_settings(durable_tasks_enabled=False))
        service._enqueue_analysis(42, "req-104-1", version=3)

    assert registered == []
    assert queued == [((42, "req-104-1"), {"analysis_expected_version": 3})]


def test_legacy_mode_does_not_drop_the_order_when_the_broker_is_down() -> None:
    """关模式 + 非生产 + 入队失败：不抛 503，工单保留（开发/测试继续跑）。"""
    with patch("app.worker.enqueue.enqueue_celery_task", lambda **kwargs: False):
        service = _service(_settings(durable_tasks_enabled=False, app_env="development"))
        assert service._enqueue_analysis(42, "req-104-1", version=3) is None


def test_legacy_mode_refuses_in_production_rather_than_losing_the_analysis() -> None:
    """关模式 + 生产 + 入队失败：503 回滚，不留「有单无任务」。"""
    from app.core.errors import AppError

    production = _settings().model_copy(
        update={"app_env": "production", "durable_tasks_enabled": False}
    )
    with patch("app.worker.enqueue.enqueue_celery_task", lambda **kwargs: False):
        with pytest.raises(AppError) as excinfo:
            _service(production)._enqueue_analysis(42, "req-104-1", version=3)

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "AI.ENQUEUE_FAILED"


def test_production_assembles_with_the_default_settings() -> None:
    """默认开启后，生产不再需要显式 DURABLE_TASKS_ENABLED=true 才能启动。"""
    from app.core.startup import verify_task_delivery_configuration

    production = _settings().model_copy(update={"app_env": "production"})
    verify_task_delivery_configuration(production)  # 不抛即通过

    with pytest.raises(RuntimeError, match="DURABLE_TASKS_ENABLED"):
        verify_task_delivery_configuration(
            production.model_copy(update={"durable_tasks_enabled": False})
        )
