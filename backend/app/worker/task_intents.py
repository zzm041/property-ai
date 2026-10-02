"""Bounded durable publication and execution through existing domain Services."""
import threading

from kombu.exceptions import OperationalError as BrokerOperationalError
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import begin_owned_transaction, create_database_engine, retryable_mysql_analysis_error_code
from app.core.logging import get_logger
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError
from app.repository.task_intent import TaskIntentRepository
from app.service.task_intent import (
    ANALYZE, INGEST, DELIVER, LEASE_KEY, IntentLeaseLost, IntentTiming,
    TaskIntentService, require_execution_lease,
)
from app.worker.celery_app import celery_app

logger = get_logger(__name__)


def publish_due(engine, settings, *, intent_ids=None, timing=None, checkpoint=None):
    if not settings.durable_tasks_enabled:
        raise ValueError("Durable publication requires an approved0012 rollout")
    if settings.app_env == "test" and not intent_ids:
        raise ValueError("Isolated test recovery requires explicit new intent IDs")
    if (celery_app.conf.broker_url != settings.celery_broker_url
            or celery_app.conf.task_default_queue != settings.resolved_celery_queue):
        raise ValueError("Publisher app broker/vhost/queue differs from approved settings")
    from app.core.time import utc_now
    with Session(engine) as session, begin_owned_transaction(session):
        ids = TaskIntentRepository(session).due_ids(utc_now(), intent_ids=intent_ids)
    sent = 0
    for intent_id in ids:
        with Session(engine, expire_on_commit=False) as session:
            lease = TaskIntentService(session, timing=timing).claim_publication(intent_id)
        if lease is None:
            continue
        try:
            execute_task_intent_task.apply_async(args=(intent_id,), task_id=lease["task_id"],
                queue=settings.resolved_celery_queue, delivery_mode=2, retry=False)
            if checkpoint:
                checkpoint("published_before_writeback", intent_id)
            success = True
        except (BrokerOperationalError, OSError, TimeoutError):
            success = False
        with Session(engine, expire_on_commit=False) as session:
            TaskIntentService(session, timing=timing).publication_result(intent_id, lease["token"], sent=success)
        sent += int(success)
    return sent


def _heartbeat(engine, lease, timing, stop):
    while not stop.wait(timing.heartbeat):
        try:
            with Session(engine, expire_on_commit=False) as session:
                if not TaskIntentService(session, timing=timing).heartbeat(lease):
                    return
        except SQLAlchemyError:
            # No false renewal: subsequent business writes recheck actual DB lease.
            logger.warning("task_intent_heartbeat_not_persisted", intent_id=lease["id"])
            return


def _run_domain(session, settings, lease):
    session.info[LEASE_KEY] = lease
    if lease["operation"] == ANALYZE:
        from app.service.analysis import AnalysisService
        from app.repository.work_order import WorkOrderRepository
        from app.worker.tasks import raise_retryable_analysis_failure, _record_analysis_attempt_outcome
        with begin_owned_transaction(session):
            repo = WorkOrderRepository(session)
            work = repo.get_work_order_for_update(lease["resource_id"])
            require_execution_lease(session)
            if work is None or (work.tenant_id, work.property_id) != (lease["tenant_id"], lease["property_id"]):
                return "CANCELLED", None, False
            latest = repo.latest_analysis_attempt(work.id)
            previous = latest.payload_json if latest and latest.payload_json.get("task_id") == lease["task_id"] else None
            attachment_completion = repo.attachment_completion_request(
                work.id,
                lease["task_id"],
            ) is not None
        context = {"task_id": lease["task_id"], "attempt": lease["attempt"],
                   "execution_token": lease["token"], "expected_version": lease["resource_version"],
                   "claim": previous, "attachment_completion": attachment_completion}
        analysis = AnalysisService(session, settings=settings).analyze_work_order(
            work_order_id=lease["resource_id"], request_id=lease["request_id"],
            attempt=lease["attempt"], task_context=context)
        raise_retryable_analysis_failure(analysis)
        _record_analysis_attempt_outcome(lease["task_id"], analysis)
        status = getattr(analysis, "status", None)
        if status in {"COMPLETED", "LOW_CONFIDENCE"}:
            return "SUCCEEDED", None, False
        if status == "FAILED":
            return "FAILED", getattr(analysis, "error_code", "AI.INVALID_OUTPUT"), False
        # A result may have committed before the previous executor died.
        with begin_owned_transaction(session):
            work = repo.get_work_order_for_update(lease["resource_id"])
            require_execution_lease(session)
            latest = repo.latest_analysis_attempt(lease["resource_id"])
            if (work and work.current_assignment_id is not None and latest
                    and latest.payload_json.get("task_id") == lease["task_id"]):
                return "SUCCEEDED", None, False
        return "CANCELLED", None, False
    if lease["operation"] == INGEST:
        from app.model.knowledge import KnowledgeIngestionJob
        from app.service.knowledge import KnowledgeService
        with begin_owned_transaction(session):
            require_execution_lease(session)
            job = session.get(KnowledgeIngestionJob, lease["resource_id"])
            if job is None or (job.tenant_id, job.property_id, job.version) != (lease["tenant_id"], lease["property_id"], lease["resource_version"]):
                return "CANCELLED", None, False
            document_id, version = job.document_id, job.version
        outcome = KnowledgeService(session, settings=settings).ingest_version(
            document_id=document_id, version=version, request_id=lease["request_id"])
        with begin_owned_transaction(session):
            job = session.get(KnowledgeIngestionJob, lease["resource_id"], populate_existing=True)
            code = job.error_code if job else "TASK.RESOURCE_MISSING"
        if outcome == "SUCCEEDED":
            return "SUCCEEDED", None, False
        from app.core.errors import KnowledgeErrorCode
        return "FAILED", code, code == KnowledgeErrorCode.UNAVAILABLE
    if lease["operation"] == DELIVER:
        from app.model.ai import Notification
        from app.model.knowledge import NotificationDelivery
        from app.service.notification import NotificationService
        with begin_owned_transaction(session):
            require_execution_lease(session)
            delivery = session.get(NotificationDelivery, lease["resource_id"])
            notification = session.get(Notification, delivery.notification_id) if delivery else None
            if notification is None or (notification.tenant_id, notification.property_id) != (lease["tenant_id"], lease["property_id"]):
                return "CANCELLED", None, False
        outcome = NotificationService(session, settings=settings).deliver_pending(lease["resource_id"])
        if session.in_transaction():
            session.rollback()  # _delivery_outcome is read-only and may autobegin.
        if outcome == "SUCCEEDED":
            return "SUCCEEDED", None, False
        return "FAILED", "TASK.NOTIFICATION_UNAVAILABLE", outcome in {"RETRY", "UNCERTAIN"}
    raise ValueError("Unsupported durable operation")


def execute_intent(engine, settings, intent_id, task_id, *, timing=None, checkpoint=None):
    if not settings.durable_tasks_enabled:
        raise ValueError("Durable execution requires an approved0012 rollout")
    timing = timing or IntentTiming()
    with Session(engine, expire_on_commit=False) as session:
        lease = TaskIntentService(session, timing=timing).claim_execution(intent_id, task_id)
    if lease is None:
        return "IGNORED"
    if checkpoint:
        checkpoint("claimed_before_domain", intent_id)
    stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(engine, lease, timing, stop), daemon=True)
    heartbeat.start()
    try:
        try:
            with Session(engine, autoflush=False, expire_on_commit=False) as session:
                outcome, code, retryable = _run_domain(session, settings, lease)
        except IntentLeaseLost:
            return "LEASE_LOST"
        except (ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError) as exc:
            outcome, code, retryable = "FAILED", exc.error_code, True
        except OperationalError as exc:
            retryable = retryable_mysql_analysis_error_code(exc) is not None
            outcome, code = "FAILED", "TASK.DATABASE_TRANSIENT" if retryable else "TASK.DATABASE_PERMANENT"
        except Exception as exc:
            outcome, code, retryable = "FAILED", "TASK.PERMANENT_FAILURE", False
            logger.error("task_intent_permanent_failure", intent_id=intent_id, error_type=type(exc).__name__)
        # New connection/session after every attempt, particularly a broken DB transaction.
        if checkpoint:
            checkpoint("domain_committed_before_intent_finish", intent_id)
        with Session(engine, expire_on_commit=False) as session:
            persisted = TaskIntentService(session, timing=timing).finish(lease, outcome, error=code, retryable=retryable)
            from app.model.task_intent import TaskIntent
            stored = session.get(TaskIntent, intent_id) if persisted else None
            durable_outcome = stored.status if stored is not None else "LEASE_LOST"
        if checkpoint:
            checkpoint("result_committed_before_ack", intent_id)
        return durable_outcome
    except SQLAlchemyError:
        logger.error("task_intent_result_not_persisted", intent_id=intent_id, db_persisted=False)
        return "DB_UNAVAILABLE_LEASE_RECOVERY_REQUIRED"
    finally:
        stop.set()
        heartbeat.join(timeout=1)


@celery_app.task(bind=True, name="app.worker.task_intents.execute_task_intent_task", acks_late=True, max_retries=0)
def execute_task_intent_task(self, intent_id):
    settings = get_settings()
    engine = create_database_engine(settings.active_database_url)
    try:
        return execute_intent(engine, settings, intent_id, str(self.request.id))
    finally:
        engine.dispose()


def run_publisher(engine, settings, *, ids_supplier, seconds, timing=None, checkpoint=None, stop=None):
    """One supervised process, bounded batches and lifetime; never scan orders."""
    import time
    if not 0 < seconds <= 3600:
        raise ValueError("Publisher needs a finite supervised lifetime up to one hour")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not (stop and stop.is_set()):
        ids = ids_supplier()
        if ids or (ids is None and settings.app_env != "test"):
            try:
                publish_due(engine, settings, intent_ids=ids, timing=timing, checkpoint=checkpoint)
            except SQLAlchemyError:
                # DB-backed intent remains owed; no fake success or memory-only retry.
                logger.warning("task_intent_publisher_database_unavailable", db_persisted=False)
        time.sleep(min(2, max(0, deadline-time.monotonic())))
