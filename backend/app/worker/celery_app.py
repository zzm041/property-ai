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
        imports=("app.worker.tasks",),
    )
    return app


celery_app = create_celery_app()
