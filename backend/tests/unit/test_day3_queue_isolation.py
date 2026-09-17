"""Day 3 queue isolation, pool defaults, and enqueue routing."""

from unittest.mock import MagicMock

from app.core.config import Settings
from app.core.database import create_database_engine
from app.worker.celery_app import create_celery_app
from app.worker.enqueue import enqueue_celery_task


def _settings(app_env: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": app_env,
        "dev_auth_enabled": False,
        "wechat_login_enabled": False,
    }
    if app_env == "production":
        values["app_secret_key"] = "production-secret-that-is-long-enough"
    values.update(overrides)
    return Settings(**values)


def test_celery_queue_names_are_isolated_by_app_env() -> None:
    queues = {
        env: _settings(env).resolved_celery_queue
        for env in ("development", "test", "staging", "production")
    }
    assert queues == {
        "development": "property_ai.development",
        "test": "property_ai.test",
        "staging": "property_ai.staging",
        "production": "property_ai.production",
    }
    assert len(set(queues.values())) == 4
    prod = _settings("production")
    assert prod.resolved_celery_dlq == "property_ai.production.dlq"
    assert prod.resolved_celery_dlq != prod.resolved_celery_queue


def test_explicit_celery_task_queue_override() -> None:
    settings = _settings("production", celery_task_queue="property_ai.custom")
    assert settings.resolved_celery_queue == "property_ai.custom"
    assert settings.resolved_celery_dlq == "property_ai.custom.dlq"


def test_create_celery_app_listens_only_to_env_queue(monkeypatch) -> None:
    settings = _settings("staging")
    monkeypatch.setattr("app.worker.celery_app.get_settings", lambda: settings)
    app = create_celery_app()
    assert app.conf.task_default_queue == "property_ai.staging"
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    queue_names = {item.name for item in app.conf.task_queues}
    assert queue_names == {"property_ai.staging", "property_ai.staging.dlq"}


def test_production_enqueue_uses_isolated_queue(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = _settings("production")
    task = MagicMock()
    ok = enqueue_celery_task(
        settings=settings,
        celery_task=task,
        args=(11, "rid"),
        event="ai_enqueue_failed",
        extra={"work_order_id": 11},
    )
    assert ok is True
    task.apply_async.assert_called_once_with(
        args=(11, "rid"),
        queue="property_ai.production",
    )
    task.delay.assert_not_called()


def test_sqlalchemy_v1_pool_defaults() -> None:
    engine = create_database_engine("mysql+pymysql://property:x@127.0.0.1:3306/demo")
    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 10
    assert engine.pool._recycle == 1800
    assert engine.pool._pre_ping is True
    engine.dispose()
