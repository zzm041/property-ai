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
    background: Any = None,
    in_process: Callable[..., None] | None = None,
    in_process_args: Sequence[Any] | None = None,
) -> bool:
    """Schedule a Celery task. Return True when work was handed off.

    - test / pytest: BackgroundTasks in-process when provided; never hits the broker
    - production: Celery only; failure is logged and not converted to in-process work
    - development: Celery first, then BackgroundTasks fallback
    """
    payload = dict(extra or {})
    process_args = tuple(in_process_args if in_process_args is not None else args)

    if settings.app_env == "test" or running_under_pytest():
        if background is not None and in_process is not None:
            background.add_task(in_process, *process_args)
            return True
        return False

    try:
        celery_task.delay(*args)
        return True
    except Exception:
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
