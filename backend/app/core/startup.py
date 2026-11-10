"""Startup assembly self-checks.

PA-104-A-1: production must never start with the durable task path disabled.
Without the durable outbox an enqueue failure is either raised after the
business rows are written (PA-104-A) or, worse, silently lost. The check
belongs to assembly (``create_app``), not to individual call sites.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DURABLE_TASKS_FIX_HINT = (
    "Set DURABLE_TASKS_ENABLED=true in production so task intents are persisted "
    "before the worker hand-off (see docs/09-DEPLOYMENT.md)."
)


def verify_task_delivery_configuration(settings: Settings) -> None:
    """Refuse to assemble a production app without durable task delivery."""

    if settings.app_env != "production":
        return
    if settings.durable_tasks_enabled:
        return

    message = (
        "DURABLE_TASKS_ENABLED must be true when APP_ENV=production: without the "
        "durable task path a failed enqueue leaves a committed business row with no "
        "queued task and no persisted intent. " + DURABLE_TASKS_FIX_HINT
    )
    logger.error(
        "durable_task_delivery_disabled",
        app_env=settings.app_env,
        durable_tasks_enabled=bool(settings.durable_tasks_enabled),
        fix=DURABLE_TASKS_FIX_HINT,
    )
    raise RuntimeError(message)
