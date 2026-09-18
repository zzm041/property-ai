"""Build Celery with environment-isolated queues and bounded retries."""

from celery import Celery
from celery.signals import (
    task_failure,
    task_prerun,
    task_success,
    worker_process_init,
)
from kombu import Exchange, Queue
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_celery_app() -> Celery:
    settings = get_settings()
    queue_name = settings.resolved_celery_queue
    dlq_name = settings.resolved_celery_dlq
    exchange = Exchange(queue_name, type="direct", durable=True)
    app = Celery("property_ai", broker=settings.celery_broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_transport_options={"confirm_publish": True},
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_default_retry_delay=2,
        task_default_queue=queue_name,
        task_default_exchange=queue_name,
        task_default_routing_key=queue_name,
        task_queues=(
            Queue(queue_name, exchange=exchange, routing_key=queue_name, durable=True),
            Queue(
                dlq_name,
                exchange=Exchange(dlq_name, type="direct", durable=True),
                routing_key=dlq_name,
                durable=True,
            ),
        ),
        task_annotations={
            "app.worker.tasks.analyze_work_order_task": {"max_retries": 2},
            "app.worker.tasks.ingest_knowledge_task": {"max_retries": 2},
            "app.worker.tasks.deliver_notification_task": {"max_retries": 3},
        },
        imports=("app.worker.tasks",),
    )
    return app


celery_app = create_celery_app()


@worker_process_init.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    settings = get_settings()
    configure_logging(debug=settings.app_debug, service="worker")
    bind_contextvars(service="worker", app_env=settings.app_env)


@task_prerun.connect
def _bind_task_context(task_id: str | None = None, task=None, args=None, **_kwargs) -> None:
    clear_contextvars()
    payload: dict[str, object] = {
        "service": "worker",
        "task_id": task_id,
        "operation": getattr(task, "name", None),
    }
    name = getattr(task, "name", "") or ""
    values = tuple(args or ())
    if "analyze_work_order" in name and values:
        payload["work_order_id"] = values[0]
        if len(values) > 1:
            payload["request_id"] = values[1]
    elif "ingest_knowledge" in name and len(values) >= 3:
        payload["document_id"] = values[0]
        payload["request_id"] = values[2]
    elif "deliver_notification" in name and values:
        payload["delivery_id"] = values[0]
    bind_contextvars(**payload)


@task_failure.connect
def _log_permanent_or_retry_failure(
    sender=None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    einfo=None,
    **_kwargs,
) -> None:
    del einfo
    retries = int(getattr(getattr(sender, "request", None), "retries", 0) or 0)
    max_retries = int(getattr(sender, "max_retries", 0) or 0)
    error_code = getattr(exception, "error_code", type(exception).__name__ if exception else "UNKNOWN")
    from app.worker.failed_tasks import record_failed_task

    record_failed_task(
        get_settings(),
        task_id=task_id,
        operation=getattr(sender, "name", None),
        error_code=str(error_code),
        extra={},
        args=tuple(getattr(getattr(sender, "request", None), "args", ()) or ()),
    )
    logger.error(
        "celery_task_dead_lettered" if retries >= max_retries else "celery_task_failed",
        task_id=task_id,
        operation=getattr(sender, "name", None),
        error_code=error_code,
        retries=retries,
        queue=get_settings().resolved_celery_dlq,
        exc_info=exception,
    )


@task_success.connect
def _note_celery_success_is_not_business_success(sender=None, **kwargs) -> None:
    """Celery success is not WorkOrder/ingest business SUCCEEDED."""

    del sender, kwargs
    return
