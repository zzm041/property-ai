"""Celery tasks delegate to domain services. Duplicate delivery is idempotent."""

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.logging import get_logger
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError
from app.service.analysis import AnalysisService
from app.worker.celery_app import celery_app

logger = get_logger(__name__)


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
        AnalysisService(session, settings=settings).analyze_work_order(
            work_order_id=work_order_id,
            request_id=request_id,
            attempt=int(getattr(self.request, "retries", 0)) + 1,
        )
    except (ProviderTimeoutError, ProviderUnavailableError) as exc:
        logger.warning(
            "ai_analysis_retry",
            work_order_id=work_order_id,
            error_code=exc.error_code,
        )
        raise self.retry(exc=exc, countdown=2 ** int(getattr(self.request, "retries", 0)))
    finally:
        session.close()
        engine.dispose()


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

        KnowledgeService(session, settings=settings).ingest_version(
            document_id=document_id,
            version=version,
            request_id=request_id,
        )
    except (ProviderTimeoutError, ProviderUnavailableError) as exc:
        logger.warning(
            "knowledge_ingest_retry",
            document_id=document_id,
            error_code=exc.error_code,
        )
        raise self.retry(exc=exc, countdown=2 ** int(getattr(self.request, "retries", 0)))
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

        NotificationService(session, settings=settings).deliver_pending(delivery_id)
    except (ProviderTimeoutError, ProviderUnavailableError) as exc:
        raise self.retry(exc=exc, countdown=2 ** int(getattr(self.request, "retries", 0)))
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
