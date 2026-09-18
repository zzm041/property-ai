"""Replay a recorded failed Celery task. Operator CLI; not called at API startup."""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.worker.failed_tasks import get_failed_task, list_failed_tasks, replay_failed_task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List or replay Redis-recorded failed tasks")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--record-id", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.list:
        for item in list_failed_tasks(settings, limit=args.limit, offset=args.offset):
            print(
                f"{item.get('record_id')} {item.get('operation')} "
                f"{item.get('error_code')} {item.get('status')} "
                f"args={item.get('args')} {item.get('recorded_at')}"
            )
        return 0
    if not args.record_id:
        print("Provide --list or --record-id", file=sys.stderr)
        return 2
    current = get_failed_task(args.record_id, settings)
    if current is None:
        print(f"No record {args.record_id}", file=sys.stderr)
        return 1
    if replay_failed_task(args.record_id, settings):
        print(f"Replayed {args.record_id}")
        return 0
    print(f"No replayable record {args.record_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
