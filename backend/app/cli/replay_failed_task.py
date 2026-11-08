"""Replay a recorded failed Celery task. Operator CLI; not called at API startup."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from app.core.config import get_settings
from app.worker.failed_tasks import get_failed_task, list_failed_tasks, replay_failed_task


def format_failed_task_line(item: dict[str, Any]) -> str:
    """Stable field=value line. Resource IDs are exact fields, not substrings."""

    operation = str(item.get("operation") or "")
    args = list(item.get("args") or [])
    fields = [
        f"record_id={item.get('record_id') or ''}",
        f"operation={operation}",
        f"original_task_id={item.get('task_id') or ''}",
        f"replay_task_id={item.get('replay_task_id') or ''}",
        f"status={item.get('status') or ''}",
        f"error_code={item.get('error_code') or ''}",
    ]
    if operation.endswith("analyze_work_order_task") and args:
        fields.append(f"work_order_id={args[0]}")
        if len(args) > 1:
            fields.append(f"request_id={args[1]}")
    elif operation.endswith("ingest_knowledge_task") and len(args) >= 3:
        fields.append(f"document_id={args[0]}")
        fields.append(f"version={args[1]}")
        fields.append(f"request_id={args[2]}")
    elif operation.endswith("deliver_notification_task") and args:
        fields.append(f"delivery_id={args[0]}")
    return " ".join(fields)


def parse_failed_task_line(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split():
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key:
            fields[key] = value
    return fields


def match_failed_task_records(
    text: str,
    *,
    operation: str,
    resource_key: str,
    resource_id: int | str,
    request_id: str | None = None,
) -> list[dict[str, str]]:
    wanted = str(resource_id)
    matches: list[dict[str, str]] = []
    for line in text.splitlines():
        fields = parse_failed_task_line(line)
        if fields.get("operation") != operation:
            continue
        if fields.get(resource_key) != wanted:
            continue
        if request_id is not None and fields.get("request_id") != request_id:
            continue
        matches.append(fields)
    return matches


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
            print(format_failed_task_line(item))
        return 0
    if not args.record_id:
        print("Provide --list or --record-id", file=sys.stderr)
        return 2
    current = get_failed_task(args.record_id, settings)
    if current is None:
        print(f"No record {args.record_id}", file=sys.stderr)
        return 1
    if replay_failed_task(args.record_id, settings):
        print(
            " ".join(
                [
                    "replayed",
                    f"record_id={current.get('record_id') or args.record_id}",
                    f"operation={current.get('operation') or ''}",
                    f"original_task_id={current.get('task_id') or ''}",
                    f"request_id={_request_id_from_record(current)}",
                ]
            )
        )
        return 0
    print(f"No replayable record {args.record_id}", file=sys.stderr)
    return 1


def _request_id_from_record(item: dict[str, Any]) -> str:
    args = list(item.get("args") or [])
    operation = str(item.get("operation") or "")
    if operation.endswith("analyze_work_order_task") and len(args) > 1:
        return str(args[1])
    if operation.endswith("ingest_knowledge_task") and len(args) > 2:
        return str(args[2])
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
