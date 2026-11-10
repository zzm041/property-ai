"""PA-104-A：生产环境下入队失败不得留下"DB 有单 / 队列无任务 / 无补偿行"的孤儿。

修前（HEAD f98943a，`_enqueue_analysis` 丢弃 `enqueue_celery_task` 返回值）：
`apply_async` 抛 OSError，业务事务照常提交，API 返回 2xx，分析永久丢失。
修后：生产环境抛 `AI.ENQUEUE_FAILED`(503)，所属事务回滚，孤儿状态不存在。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AiErrorCode, AppError
from app.service.work_order import WorkOrderService

ANALYZE_TASK = "app.worker.tasks.analyze_work_order_task.apply_async"


def _production() -> Settings:
    # 只为取 app_env：用开发配置构造再切环境（生产构造器要求企业凭证）。
    # PA-104 起 durable 链默认开启，而本文件考的是**关闭时**的直投路径，故显式关掉。
    return Settings(_env_file=None).model_copy(
        update={"app_env": "production", "durable_tasks_enabled": False}
    )


def _service(session: Session, settings: Settings) -> WorkOrderService:
    service = WorkOrderService.__new__(WorkOrderService)
    service._session = session
    service._settings = settings
    return service


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
    return engine


def _rows(engine) -> int:
    with Session(engine) as check:
        return int(check.execute(text("SELECT COUNT(*) FROM probe")).scalar_one())


def _run(session: Session, service: WorkOrderService) -> None:
    """等价建单事务：先落业务行，再入队分析。"""

    def operation():
        session.execute(text("INSERT INTO probe (id) VALUES (1)"))
        service._enqueue_analysis(1, "req-104a")

    service._execute(operation)


def _boom(*args, **kwargs):
    raise OSError("broker unreachable")


def test_production_enqueue_failure_raises_and_leaves_no_orphan():
    engine = _engine()
    recorded: list[dict] = []
    with Session(engine) as session:
        service = _service(session, _production())
        with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
                patch(ANALYZE_TASK, _boom), \
                patch("app.worker.failed_tasks.record_failed_task", lambda *a, **k: recorded.append(k)):
            with pytest.raises(AppError) as excinfo:
                _run(session, service)

    error = excinfo.value
    assert error.code == AiErrorCode.ENQUEUE_FAILED
    assert error.code == "AI.ENQUEUE_FAILED"
    assert error.status_code == 503
    # 孤儿判据：事务已回滚，DB 里没有这条业务行
    assert _rows(engine) == 0
    # 入队确实尝试过并失败（不是"根本没触发"）
    assert recorded and recorded[0].get("error_code") == "ENQUEUE_FAILED"


def test_production_enqueue_success_commits():
    engine = _engine()
    with Session(engine) as session:
        service = _service(session, _production())
        with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
                patch(ANALYZE_TASK, lambda *a, **k: None):
            _run(session, service)
    assert _rows(engine) == 1


def test_non_production_keeps_previous_log_only_behaviour():
    engine = _engine()
    recorded: list[dict] = []
    with Session(engine) as session:
        service = _service(
            session, Settings(_env_file=None, durable_tasks_enabled=False)
        )  # development，直投路径
        with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
                patch(ANALYZE_TASK, _boom), \
                patch("app.worker.failed_tasks.record_failed_task", lambda *a, **k: recorded.append(k)):
            _run(session, service)  # 不抛

    assert _rows(engine) == 1  # 非生产沿用旧行为：建单成功
    assert recorded and recorded[0].get("error_code") == "ENQUEUE_FAILED"


def test_durable_mode_never_hits_the_broker():
    engine = _engine()
    with Session(engine) as session:
        service = _service(session, _production().model_copy(update={"durable_tasks_enabled": True}))
        with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
                patch(ANALYZE_TASK, _boom):
            _run(session, service)  # durable 补偿由 publisher 负责，不入队也不报错
    assert _rows(engine) == 1
