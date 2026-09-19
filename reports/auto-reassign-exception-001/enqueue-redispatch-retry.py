"""Enqueue one isolated redispatch retry after correcting synthetic capacity."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
os.chdir(BACKEND)

from auth001_isolated_runtime import apply_isolated_environ, build_isolated_settings


def main() -> int:
    settings = build_isolated_settings(enable_enqueue=True)
    apply_isolated_environ(settings)

    from app.worker.enqueue import enqueue_celery_task
    from app.worker.tasks import analyze_work_order_task

    request_id = f"auto-reassign-fixture-retry-{uuid4().hex}"
    queued = enqueue_celery_task(
        settings=settings,
        celery_task=analyze_work_order_task,
        args=(7, request_id),
        event="auto_reassign_fixture_retry_enqueue_failed",
        extra={"work_order_id": 7},
    )
    print(
        json.dumps(
            {
                "task_id": "AUTO-REASSIGN-EXCEPTION-001-FIXTURE-RETRY",
                "work_order_id": 7,
                "queued": queued,
                "queue": settings.resolved_celery_queue,
                "request_id_prefix": "auto-reassign-fixture-retry-",
            }
        )
    )
    return 0 if queued else 2


if __name__ == "__main__":
    raise SystemExit(main())
