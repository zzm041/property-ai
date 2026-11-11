"""Celery task delegates to AnalysisService and is logically idempotent."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.database import retryable_mysql_transaction_error_code
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


def test_scoped_sla_task_does_not_scan_unrelated_dispatch(monkeypatch) -> None:
    engine = MagicMock()
    session = MagicMock()
    completion = MagicMock()
    completion.as_dict.return_value = {
        "scanned": 1,
        "warnings": 1,
        "overdue": 0,
        "stopped": 0,
        "notifications": 2,
    }
    assignment = MagicMock()
    assignment.as_dict.return_value = {
        "scanned": 0,
        "accept_timeouts": 0,
        "response_timeouts": 0,
        "notifications": 0,
        "redispatch_enqueued": 0,
    }
    service = MagicMock()
    service.sweep_due.return_value = completion
    service.sweep_assignment_deadlines.return_value = assignment
    settings = SimpleNamespace(active_database_url="mysql+pymysql://ignored")

    monkeypatch.setattr("app.worker.tasks.get_settings", lambda: settings)
    monkeypatch.setattr("app.worker.tasks.create_database_engine", lambda _url: engine)
    monkeypatch.setattr("app.worker.tasks.Session", lambda **_kwargs: session)
    monkeypatch.setattr("app.service.work_order.sla.SlaService", lambda *_args, **_kwargs: service)
    analysis = MagicMock()
    monkeypatch.setattr("app.worker.tasks.AnalysisService", analysis)

    result = sweep_sla_task.run(501)

    service.sweep_due.assert_called_once_with(work_order_id=501)
    service.sweep_assignment_deadlines.assert_called_once_with(work_order_id=501)
    service.sweep_unavailable_assignments.assert_not_called()
    analysis.assert_not_called()
    assert result["completion_warnings"] == 1
    assert result["dispatch_scanned"] == 0
    session.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()
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


@pytest.mark.parametrize("code", [1205, 1213])
def test_mysql_lock_failures_are_retryable(code: int) -> None:
    exc = OperationalError("statement", {}, Exception(code, "transient lock failure"))

    assert retryable_mysql_transaction_error_code(exc) == code


@pytest.mark.parametrize("code", [1045, 1062, 1146])
def test_other_mysql_operational_failures_are_not_retried(code: int) -> None:
    exc = OperationalError("statement", {}, Exception(code, "permanent database failure"))

    assert retryable_mysql_transaction_error_code(exc) is None


def test_unstructured_operational_failure_is_not_retried() -> None:
    exc = OperationalError("statement", {}, Exception("connection closed"))

    assert retryable_mysql_transaction_error_code(exc) is None


def test_analysis_task_rolls_back_and_retries_mysql_deadlock(monkeypatch) -> None:
    class RetryScheduled(Exception):
        pass

    engine = MagicMock()
    session = MagicMock()
    service = MagicMock()
    deadlock = OperationalError("statement", {}, Exception(1213, "deadlock"))
    service.analyze_work_order.side_effect = deadlock
    retry = MagicMock(side_effect=RetryScheduled)
    monkeypatch.setattr("app.worker.tasks.create_database_engine", lambda _url: engine)
    monkeypatch.setattr("app.worker.tasks.Session", lambda **_kwargs: session)
    monkeypatch.setattr("app.worker.tasks.AnalysisService", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(analyze_work_order_task, "retry", retry)

    with pytest.raises(RetryScheduled):
        analyze_work_order_task.run(21, "incident-concurrent-request")

    session.invalidate.assert_called_once_with()
    retry.assert_called_once_with(exc=deadlock, countdown=1, headers={"analysis_recovery": None})
    session.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()
