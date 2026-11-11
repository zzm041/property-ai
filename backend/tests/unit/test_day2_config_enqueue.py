"""Day 2 production configuration fail-fast and enqueue semantics."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.worker.enqueue import enqueue_celery_task


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "app_secret_key": "production-secret-that-is-long-enough",
        "dev_auth_enabled": False,
        "wechat_login_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_llm_missing_credentials_fail_fast() -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        _production(llm_provider="production")


def test_production_storage_missing_credentials_fail_fast() -> None:
    with pytest.raises(ValidationError, match="STORAGE_ENDPOINT"):
        _production(storage_provider="production")


def test_production_keeps_development_providers_without_live_keys() -> None:
    settings = _production()
    assert settings.llm_provider == "development"
    assert settings.should_enqueue_ai_on_create is True


def test_production_enqueue_does_not_fallback_in_process(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = _production()
    task = MagicMock()
    task.apply_async.side_effect = RuntimeError("broker down")
    in_process = MagicMock()
    ok = enqueue_celery_task(
        settings=settings,
        celery_task=task,
        args=(1, "rid"),
        event="ai_enqueue_failed",
        extra={"work_order_id": 1},
        background=MagicMock(),
        in_process=in_process,
    )
    assert ok is False
    in_process.assert_not_called()


def test_development_enqueue_falls_back_to_background(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = Settings(_env_file=None, app_env="development")
    task = MagicMock()
    task.apply_async.side_effect = RuntimeError("broker down")
    background = MagicMock()
    in_process = MagicMock()
    ok = enqueue_celery_task(
        settings=settings,
        celery_task=task,
        args=(9, 1, "rid"),
        event="knowledge_ingest_enqueue_failed",
        background=background,
        in_process=in_process,
    )
    assert ok is True
    background.add_task.assert_called_once()


def test_pytest_does_not_hit_broker_in_test_env() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_enqueue_on_create=True,
        wechat_login_enabled=False,
    )
    task = MagicMock()
    background = MagicMock()
    in_process = MagicMock()
    ok = enqueue_celery_task(
        settings=settings,
        celery_task=task,
        args=(3, "rid"),
        event="ai_enqueue_failed",
        background=background,
        in_process=in_process,
    )
    assert ok is True
    task.apply_async.assert_not_called()
    background.add_task.assert_called_once()


def test_live_test_env_enqueues_to_celery(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_enqueue_on_create=True,
        wechat_login_enabled=False,
        celery_task_queue="property_ai.day4",
    )
    task = MagicMock()
    task.name = "app.worker.tasks.analyze_work_order_task"
    ok = enqueue_celery_task(
        settings=settings,
        celery_task=task,
        args=(4, "rid-4"),
        event="ai_enqueue_failed",
        extra={"work_order_id": 4},
    )
    assert ok is True
    task.apply_async.assert_called_once_with(
        args=(4, "rid-4"),
        queue="property_ai.day4",
    )
