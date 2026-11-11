"""PA-104-A-1：启动自检 + 非生产入队失败必须留下可枚举的失败记录。

两小件：
1. `APP_ENV=production` 且 `DURABLE_TASKS_ENABLED=false` 时，装配层拒绝启动
   （`create_app`），不是散落在调用点的静默降级。
2. 非生产环境入队失败时仍写 `record_failed_task(error_code=ENQUEUE_FAILED)`，
   使"DB 有单、队列无任务"的孤儿态可被 `list_failed_tasks` 查出；若记录本身
   写不进去（Redis 与 DLQ 都失败），必须 ERROR 级显式报出，不留无声孤儿。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from app.worker.enqueue import enqueue_celery_task
from app.worker.failed_tasks import list_failed_tasks

ANALYZE_TASK_NAME = "app.worker.tasks.analyze_work_order_task"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _production(durable: bool) -> Settings:
    # 生产构造器要求企业凭证，这里只切 app_env/durable 两个开关
    # PA-165: create_app 在生产下拒绝 development 的 RAG 三件，所以顺带把它们
    # 指到真实端点——本文件只关心 durable 落库与自检，RAG provider 真伪由
    # test_rag_provider_startup_guard.py 负责。
    return _settings().model_copy(
        update={
            "app_env": "production",
            "durable_tasks_enabled": durable,
            "embedding_provider": "production",
            "embedding_endpoint": "http://host.docker.internal:11436/v1/embeddings",
            "reranker_provider": "production",
            "reranker_endpoint": "http://host.docker.internal:11436/v1/rerank",
            "rag_llm_provider": "production",
            "rag_llm_base_url": "http://host.docker.internal:11436/v1",
            "rag_llm_api_key": "test-rag-key",
        }
    )


class _FakePipeline:
    def __init__(self, store: "_FakeRedis") -> None:
        self._store = store

    def hset(self, key, field, value):
        self._store.hashes.setdefault(key, {})[field] = value
        return self

    def zadd(self, key, mapping):
        self._store.zsets.setdefault(key, {}).update(mapping)
        return self

    def execute(self):
        return [True]


class _FakeRedis:
    """只实现 failed_tasks 用到的四个调用：pipeline/hset/zadd/execute + zrevrange/hget。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return _FakePipeline(self)

    def zrevrange(self, key, start, stop):
        members = sorted(
            self.zsets.get(key, {}).items(), key=lambda item: item[1], reverse=True
        )
        return [name for name, _ in members][start : stop + 1]

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def close(self) -> None:
        return None


def _boom(*_args, **_kwargs):
    raise OSError("broker unreachable")


def test_production_without_durable_tasks_refuses_to_assemble():
    with patch("app.core.startup.logger") as startup_logger:
        with pytest.raises(RuntimeError) as excinfo:
            create_app(_production(durable=False))

    assert "DURABLE_TASKS_ENABLED" in str(excinfo.value)
    startup_logger.error.assert_called_once()
    event = startup_logger.error.call_args.args[0]
    assert event == "durable_task_delivery_disabled"
    assert startup_logger.error.call_args.kwargs["fix"]


def test_production_with_durable_tasks_assembles():
    app = create_app(_production(durable=True))
    assert isinstance(app, FastAPI)


def test_development_without_durable_tasks_assembles_silently():
    with patch("app.core.startup.logger") as startup_logger:
        app = create_app(_settings())
    assert isinstance(app, FastAPI)
    startup_logger.error.assert_not_called()


def test_development_enqueue_failure_leaves_enumerable_record():
    settings = _settings()
    fake = _FakeRedis()
    task = MagicMock()
    task.name = ANALYZE_TASK_NAME
    task.apply_async.side_effect = OSError("broker unreachable")

    with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
            patch("redis.Redis.from_url", lambda *a, **k: fake), \
            patch("app.worker.failed_tasks._publish_dlq", lambda *a, **k: None):
        handed_off = enqueue_celery_task(
            settings=settings,
            celery_task=task,
            args=(28, "rid-104a1"),
            event="analysis_enqueue_failed",
        )
        records = list_failed_tasks(settings)

    assert handed_off is False
    assert len(records) == 1
    record = records[0]
    assert record["error_code"] == "ENQUEUE_FAILED"
    assert record["operation"] == ANALYZE_TASK_NAME
    assert record["args"] == [28, "rid-104a1"]
    assert record["status"] == "RECORDED"
    assert record["record_id"].startswith("enqueue-")


def test_unrecordable_enqueue_failure_is_reported():
    settings = _settings()
    task = MagicMock()
    task.name = ANALYZE_TASK_NAME
    task.apply_async.side_effect = OSError("broker unreachable")

    with patch("app.worker.enqueue.running_under_pytest", lambda: False), \
            patch("app.worker.failed_tasks.record_failed_task", lambda *a, **k: None), \
            patch("app.worker.enqueue.logger") as enqueue_logger:
        handed_off = enqueue_celery_task(
            settings=settings,
            celery_task=task,
            args=(28, "rid-104a1"),
            event="analysis_enqueue_failed",
        )

    assert handed_off is False
    events = [call.args[0] for call in enqueue_logger.error.call_args_list]
    assert "enqueue_failure_unrecorded" in events
