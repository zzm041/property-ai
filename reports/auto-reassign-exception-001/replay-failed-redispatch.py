"""Replay only the isolated failed redispatch for this evidence work order."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


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

    from app.worker.failed_tasks import list_failed_tasks, replay_failed_task

    matches = []
    for item in list_failed_tasks(settings, limit=100, offset=0):
        args = list(item.get("args") or [])
        if (
            item.get("operation") == "app.worker.tasks.analyze_work_order_task"
            and args
            and int(args[0]) == 7
            and item.get("error_code") == "TransactionContractError"
        ):
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one exact failed redispatch; found {len(matches)}")
    item = matches[0]
    record_id = str(item["record_id"])
    replayed = replay_failed_task(record_id, settings)
    print(
        json.dumps(
            {
                "task_id": "AUTO-REASSIGN-EXCEPTION-001-RECOVERY",
                "work_order_id": 7,
                "record_id": record_id,
                "original_error_code": item.get("error_code"),
                "replayed": replayed,
                "queue": settings.resolved_celery_queue,
            },
            ensure_ascii=False,
        )
    )
    return 0 if replayed else 2


if __name__ == "__main__":
    raise SystemExit(main())
