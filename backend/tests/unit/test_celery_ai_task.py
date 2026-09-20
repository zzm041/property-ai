"""Celery task delegates to AnalysisService and is logically idempotent."""

from types import SimpleNamespace

import pytest

from app.core.errors import AiErrorCode
from app.provider.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.worker.tasks import (
    analyze_work_order_task,
    ingest_knowledge_task,
    schedule_due_notification_deliveries_task,
    sweep_sla_task,
    raise_retryable_analysis_failure,
    run_analyze_work_order,
    run_ingest_knowledge,
)


def test_celery_task_is_registered() -> None:
    assert analyze_work_order_task.name == "app.worker.tasks.analyze_work_order_task"
    assert ingest_knowledge_task.name == "app.worker.tasks.ingest_knowledge_task"
    assert sweep_sla_task.name == "app.worker.tasks.sweep_sla_task"
    assert schedule_due_notification_deliveries_task.name == (
        "app.worker.tasks.schedule_due_notification_deliveries_task"
    )
    assert callable(run_analyze_work_order)
    assert callable(run_ingest_knowledge)
    assert analyze_work_order_task.max_retries == 2
    assert ingest_knowledge_task.max_retries == 2
    assert analyze_work_order_task.max_retries == 2
    assert ingest_knowledge_task.max_retries == 2


def test_failed_timeout_analysis_is_retryable() -> None:
    with pytest.raises(ProviderTimeoutError):
        raise_retryable_analysis_failure(
            SimpleNamespace(status="FAILED", error_code=AiErrorCode.TIMEOUT)
        )


def test_failed_unavailable_analysis_is_retryable() -> None:
    with pytest.raises(ProviderUnavailableError):
        raise_retryable_analysis_failure(
            SimpleNamespace(status="FAILED", error_code=AiErrorCode.UNAVAILABLE)
        )


def test_failed_rate_limited_analysis_is_retryable() -> None:
    with pytest.raises(ProviderRateLimitError):
        raise_retryable_analysis_failure(
            SimpleNamespace(status="FAILED", error_code=AiErrorCode.RATE_LIMITED)
        )


def test_completed_or_invalid_analysis_is_not_retried() -> None:
    raise_retryable_analysis_failure(None)
    raise_retryable_analysis_failure(
        SimpleNamespace(status="COMPLETED", error_code=None)
    )
    raise_retryable_analysis_failure(
        SimpleNamespace(status="FAILED", error_code=AiErrorCode.INVALID_OUTPUT)
    )
