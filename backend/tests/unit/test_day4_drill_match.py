"""Structured failed-task matching: exact fields, not digit-in-UUID coincidence."""

from app.cli.replay_failed_task import (
    format_failed_task_line,
    match_failed_task_records,
    parse_failed_task_line,
)


def test_match_requires_operation_and_resource_fields() -> None:
    listed = "\n".join(
        [
            format_failed_task_line(
                {
                    "record_id": "rec-real",
                    "task_id": "orig-1",
                    "operation": "app.worker.tasks.analyze_work_order_task",
                    "status": "RECORDED",
                    "error_code": "AI.UNAVAILABLE",
                    "args": [42, "d4-fail-analyze-create"],
                }
            ),
            format_failed_task_line(
                {
                    "record_id": "rec-other",
                    "task_id": "orig-2",
                    "operation": "app.worker.tasks.analyze_work_order_task",
                    "status": "RECORDED",
                    "error_code": "AI.TIMEOUT",
                    "args": [99, "other-request"],
                }
            ),
        ]
    )
    matches = match_failed_task_records(
        listed,
        operation="app.worker.tasks.analyze_work_order_task",
        resource_key="work_order_id",
        resource_id=42,
        request_id="d4-fail-analyze-create",
    )
    assert [item["record_id"] for item in matches] == ["rec-real"]
    assert matches[0]["original_task_id"] == "orig-1"


def test_uuid_or_timestamp_digits_do_not_match_resource_id() -> None:
    listed = "\n".join(
        [
            "record_id=550e8400-e29b-41d4-a716-446655440123 "
            "operation=app.worker.tasks.deliver_notification_task "
            "original_task_id=t-1 status=RECORDED delivery_id=9",
            "record_id=old-ts-20260918123456 "
            "operation=app.worker.tasks.ingest_knowledge_task "
            "original_task_id=t-2 status=RECORDED document_id=7 version=1 request_id=x",
        ]
    )
    assert (
        match_failed_task_records(
            listed,
            operation="app.worker.tasks.analyze_work_order_task",
            resource_key="work_order_id",
            resource_id=123,
        )
        == []
    )
    assert "123" in listed
    parsed = parse_failed_task_line(listed.splitlines()[0])
    assert parsed["record_id"].endswith("123")
    assert parsed.get("work_order_id") is None


def test_drill_script_no_longer_uses_substring_needle() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "ops"
        / "day4_failed_task_drill.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "match_failed_task_records" in text
    assert "needle in line" not in text
    assert "str(work_order_id) in line" not in text
