"""Celery enqueue with production fail-observable semantics.

BackgroundTasks is a Development/test fallback only. Production never
silently runs heavy ingest/analysis/notification delivery in-process.
"""

from collections.abc import Callable, Sequence
import os
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def running_under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def enqueue_celery_task(
    *,
    settings: Settings,
    celery_task: Any,
    args: Sequence[Any] = (),
    event: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    background: Any = None,
    in_process: Callable[..., None] | None = None,
    in_process_args: Sequence[Any] | None = None,
) -> bool:
    """Schedule a Celery task. Return True when work was handed off.

    - pytest: BackgroundTasks in-process when provided; never hits the broker
    - live APP_ENV=test (Day 4 E2E): Celery when not under pytest
    - production: Celery only; failure is recorded and not converted to in-process work
    - development: Celery first, then BackgroundTasks fallback
    """
    payload = dict(extra or {})
    process_args = tuple(in_process_args if in_process_args is not None else args)

    # Pytest must not hit the broker. A live APP_ENV=test process (Day 4 E2E)
    # enqueues to Celery when AI_ENQUEUE_ON_CREATE is enabled.
    if running_under_pytest():
        if background is not None and in_process is not None:
            background.add_task(in_process, *process_args)
            return True
        return False

    try:
        options = {"headers": headers} if headers else {}
        celery_task.apply_async(args=tuple(args), queue=settings.resolved_celery_queue, **options)
        return True
    except Exception:
        from app.worker.failed_tasks import record_failed_task

        record_failed_task(
            settings,
            task_id=None,
            operation=getattr(celery_task, "name", None) or event,
            error_code="ENQUEUE_FAILED",
            extra={**payload, "enqueue_event": event},
            args=tuple(args),
        )
        if settings.app_env == "production":
            logger.error(event, **payload)
            return False
        logger.warning(event, **payload)
        if background is not None and in_process is not None:
            background.add_task(in_process, *process_args)
            return True
        return False


def schedule_notification_delivery(settings: Settings, delivery_id: int) -> bool:
    from app.worker.tasks import deliver_notification_task

    return enqueue_celery_task(
        settings=settings,
        celery_task=deliver_notification_task,
        args=(delivery_id,),
        event="notification_enqueue_failed",
        extra={"delivery_id": delivery_id},
    )


def schedule_dispatch_recovery(settings: Settings) -> bool:
    """Post-commit availability hint. Durable pending + beat is the backstop."""
    if not settings.ai_auto_assign or not settings.should_enqueue_ai_on_create:
        return False
    from app.worker.tasks import retry_waiting_dispatch_task

    return enqueue_celery_task(settings=settings, celery_task=retry_waiting_dispatch_task,
                               event="dispatch_recovery_enqueue_failed")
