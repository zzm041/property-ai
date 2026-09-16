from celery import Celery

from app.core.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("property_ai", broker=settings.celery_broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_default_retry_delay=2,
        task_annotations={
            "app.worker.tasks.analyze_work_order_task": {"max_retries": 2},
            "app.worker.tasks.ingest_knowledge_task": {"max_retries": 2},
            "app.worker.tasks.deliver_notification_task": {"max_retries": 3},
        },
        imports=("app.worker.tasks",),
    )
    return app


celery_app = create_celery_app()
