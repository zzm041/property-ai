"""Celery tasks delegate to domain services. Duplicate delivery is idempotent."""

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.errors import AiErrorCode
from app.core.logging import get_logger
from app.domain.ai import AnalysisStatus
from app.provider.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.service.analysis import AnalysisService
from app.service.notification import delivery_retry_delay_seconds
from app.worker.celery_app import celery_app

logger = get_logger(__name__)

_RETRYABLE_MYSQL_TRANSACTION_ERROR_CODES = frozenset({1205, 1213})


def retryable_mysql_transaction_error_code(exc: OperationalError) -> int | None:
    """Return the MySQL code only for transient lock/deadlock failures."""

    args = getattr(getattr(exc, "orig", None), "args", ())
    if not args:
        return None
    try:
        code = int(args[0])
    except (TypeError, ValueError):
        return None
    return code if code in _RETRYABLE_MYSQL_TRANSACTION_ERROR_CODES else None


def raise_retryable_analysis_failure(analysis: object | None) -> None:
    """Surface retryable provider outcomes so Celery can retry and record failure."""

    if analysis is None:
        return
    if getattr(analysis, "status", None) != AnalysisStatus.FAILED.value:
        return
    code = getattr(analysis, "error_code", None)
    if code == AiErrorCode.TIMEOUT:
        raise ProviderTimeoutError("LLM timed out")
    if code == AiErrorCode.RATE_LIMITED:
        raise ProviderRateLimitError("LLM rate limited")
    if code == AiErrorCode.UNAVAILABLE:
        raise ProviderUnavailableError("LLM is unavailable")


def _record_analysis_attempt_outcome(task_id: str | None, analysis: object | None) -> None:
    if not task_id or analysis is None:
        return
    from app.worker.failed_tasks import (
        STATUS_IGNORED_EXPIRED,
        STATUS_PERMANENT_FAILED,
        STATUS_SUCCEEDED,
        complete_attempt,
    )

    status = getattr(analysis, "status", None)
    error_code = getattr(analysis, "error_code", None)
    outcome = None
    if status in {AnalysisStatus.COMPLETED.value, AnalysisStatus.LOW_CONFIDENCE.value}:
        outcome = STATUS_SUCCEEDED
    elif status == AnalysisStatus.STALE.value:
        outcome = STATUS_IGNORED_EXPIRED
    elif status == AnalysisStatus.FAILED.value and error_code == AiErrorCode.INVALID_OUTPUT:
        outcome = STATUS_PERMANENT_FAILED
    if outcome is None:
        return
    try:
        complete_attempt(str(task_id), outcome, error_code=error_code)
    except Exception:
        logger.warning("failed_task_outcome_unavailable", task_id=str(task_id), outcome=outcome)


@celery_app.task(
    bind=True,
    name="app.worker.tasks.analyze_work_order_task",
    max_retries=2,
    acks_late=True,
)
def analyze_work_order_task(self, work_order_id: int, request_id: str) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        analysis = AnalysisService(session, settings=settings).analyze_work_order(
            work_order_id=work_order_id,
            request_id=request_id,
            attempt=int(getattr(self.request, "retries", 0)) + 1,
        )
        raise_retryable_analysis_failure(analysis)
        _record_analysis_attempt_outcome(getattr(self.request, "id", None), analysis)
    except (ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError) as exc:
        logger.warning(
            "ai_analysis_retry",
            work_order_id=work_order_id,
            error_code=exc.error_code,
        )
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(2 ** retries, 30))
    except OperationalError as exc:
        mysql_error_code = retryable_mysql_transaction_error_code(exc)
        if mysql_error_code is None:
            raise
        session.rollback()
        logger.warning(
            "ai_analysis_transaction_retry",
            work_order_id=work_order_id,
            mysql_error_code=mysql_error_code,
        )
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(2 ** retries, 30))
    finally:
        session.close()
        engine.dispose()


def _record_ingest_attempt_outcome(task_id: str | None, outcome: str | None) -> None:
    if not task_id or not outcome:
        return
    from app.worker.failed_tasks import (
        STATUS_IGNORED_EXPIRED,
        STATUS_PERMANENT_FAILED,
        STATUS_SUCCEEDED,
        complete_attempt,
    )

    mapped = None
    if outcome == "SUCCEEDED":
        mapped = STATUS_SUCCEEDED
    elif outcome == "FAILED":
        mapped = STATUS_PERMANENT_FAILED
    elif outcome == "SKIPPED":
        mapped = STATUS_IGNORED_EXPIRED
    if mapped is None:
        return
    try:
        complete_attempt(str(task_id), mapped, error_code=None if mapped == STATUS_SUCCEEDED else outcome)
    except Exception:
        logger.warning("failed_task_outcome_unavailable", task_id=str(task_id), outcome=mapped)


def _record_delivery_attempt_outcome(task_id: str | None, outcome: str | None) -> None:
    if not task_id or not outcome:
        return
    from app.worker.failed_tasks import (
        STATUS_PERMANENT_FAILED,
        STATUS_SUCCEEDED,
        complete_attempt,
    )

    mapped = None
    if outcome == "SUCCEEDED":
        mapped = STATUS_SUCCEEDED
    elif outcome == "FAILED":
        mapped = STATUS_PERMANENT_FAILED
    if mapped is None:
        return
    try:
        complete_attempt(str(task_id), mapped, error_code=None if mapped == STATUS_SUCCEEDED else outcome)
    except Exception:
        logger.warning("failed_task_outcome_unavailable", task_id=str(task_id), outcome=mapped)


@celery_app.task(
    bind=True,
    name="app.worker.tasks.ingest_knowledge_task",
    max_retries=2,
    acks_late=True,
)
def ingest_knowledge_task(
    self,
    document_id: int,
    version: int,
    request_id: str,
) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        from app.service.knowledge import KnowledgeService

        outcome = KnowledgeService(session, settings=settings).ingest_version(
            document_id=document_id,
            version=version,
            request_id=request_id,
        )
        _record_ingest_attempt_outcome(getattr(self.request, "id", None), outcome)
    except (ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError) as exc:
        logger.warning(
            "knowledge_ingest_retry",
            document_id=document_id,
            error_code=exc.error_code,
        )
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(2 ** retries, 30))
    finally:
        session.close()
        engine.dispose()


def run_ingest_knowledge(
    document_id: int,
    version: int,
    request_id: str,
    settings=None,
) -> None:
    settings = settings or get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        from app.service.knowledge import KnowledgeService

        KnowledgeService(session, settings=settings).ingest_version(
            document_id=document_id,
            version=version,
            request_id=request_id,
        )
    finally:
        session.close()
        engine.dispose()


@celery_app.task(
    bind=True,
    name="app.worker.tasks.deliver_notification_task",
    max_retries=3,
    acks_late=True,
)
def deliver_notification_task(self, delivery_id: int) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        from app.service.notification import NotificationService

        outcome = NotificationService(session, settings=settings).deliver_pending(delivery_id)
        _record_delivery_attempt_outcome(getattr(self.request, "id", None), outcome)
        if outcome == "RETRY":
            retries = int(getattr(self.request, "retries", 0))
            raise self.retry(
                exc=ProviderUnavailableError("Notification provider is unavailable"),
                countdown=delivery_retry_delay_seconds(retries + 1),
            )
    except (ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError) as exc:
        retries = int(getattr(self.request, "retries", 0))
        raise self.retry(exc=exc, countdown=min(2 ** retries, 30))
    finally:
        session.close()
        engine.dispose()


@celery_app.task(name="app.worker.tasks.sweep_sla_task", acks_late=True)
def sweep_sla_task() -> dict[str, int]:
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        from app.service.sla import SlaService

        service = SlaService(session, settings=settings)
        completion = service.sweep_due().as_dict()
        assignment = service.sweep_assignment_deadlines().as_dict()
        return {
            **{f"completion_{key}": value for key, value in completion.items()},
            **{f"assignment_{key}": value for key, value in assignment.items()},
        }
    finally:
        session.close()
        engine.dispose()


@celery_app.task(
    name="app.worker.tasks.schedule_due_notification_deliveries_task",
    acks_late=True,
)
def schedule_due_notification_deliveries_task() -> int:
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        from app.service.notification import NotificationService

        return NotificationService(session, settings=settings).schedule_due_deliveries()
    finally:
        session.close()
        engine.dispose()


def run_analyze_work_order(work_order_id: int, request_id: str, attempt: int = 1) -> None:
    """In-process entry used by tests and development smoke."""

    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        AnalysisService(session, settings=settings).analyze_work_order(
            work_order_id=work_order_id,
            request_id=request_id,
            attempt=attempt,
        )
    finally:
        session.close()
        engine.dispose()
