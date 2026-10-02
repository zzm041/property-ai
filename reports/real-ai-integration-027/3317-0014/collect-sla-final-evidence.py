"""Collect redacted, read-only evidence for one bounded 3317 SLA Beat run."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "scripts" / "ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from auth001_retry_runtime import build_settings, safe_description  # noqa: E402


def rows(connection, statement: str, values: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in row.items()
        }
        for row in connection.execute(text(statement), values).mappings()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--work-order-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.work_order_id <= 0:
        raise SystemExit("work order id must be positive")

    settings = build_settings(
        enable_enqueue=True,
        enable_test_proof=True,
        queue_name="property_ai.auth001.persist.v2.sla.acceptance",
    )
    engine = create_engine(settings.active_database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            target = rows(
                connection,
                """
                SELECT id, tenant_id, property_id, reporter_user_id, status,
                       process_reason, category, priority, version,
                       current_assignment_id, sla_due_at, created_at, updated_at
                FROM work_orders WHERE id = :id
                """,
                {"id": args.work_order_id},
            )
            if len(target) != 1:
                raise RuntimeError("exact synthetic work order is missing")
            evidence = {
                "task_id": "PRODUCTION-V1-LOCAL-FINAL-GAPS",
                "run_id": "sla-final-e034e9980f",
                "phase": args.phase,
                "observed_utc": datetime.now(UTC).isoformat(),
                "source_head": os.environ.get("AUTH001_SOURCE_HEAD", "UNKNOWN"),
                "runtime": safe_description(settings),
                "revision": revision,
                "work_order": target[0],
                "sla_application": rows(
                    connection,
                    """
                    SELECT a.id, a.work_order_id, a.policy_version_id, a.source,
                           a.accept_minutes, a.respond_minutes, a.complete_minutes,
                           a.warning_lead_minutes, a.escalation_minutes, a.applied_at,
                           v.policy_id, v.version_no, v.change_reason
                    FROM work_order_sla_applications a
                    LEFT JOIN sla_policy_versions v ON v.id = a.policy_version_id
                    WHERE a.work_order_id = :id
                    """,
                    {"id": args.work_order_id},
                ),
                "sla_clock": rows(
                    connection,
                    """
                    SELECT id, work_order_id, accept_due_at, respond_due_at,
                           complete_due_at, warning_at, state, created_at, updated_at
                    FROM sla_clocks WHERE work_order_id = :id
                    """,
                    {"id": args.work_order_id},
                ),
                "logs": rows(
                    connection,
                    """
                    SELECT id, work_order_id, actor_type, from_status, to_status,
                           process_reason, event_code, request_id, created_at
                    FROM work_order_logs WHERE work_order_id = :id ORDER BY id
                    """,
                    {"id": args.work_order_id},
                ),
                "notifications": rows(
                    connection,
                    """
                    SELECT id, tenant_id, property_id, user_id, title,
                           resource_type, resource_id, created_at
                    FROM notifications
                    WHERE resource_type = 'WORK_ORDER' AND resource_id = :id
                    ORDER BY id
                    """,
                    {"id": args.work_order_id},
                ),
                "notification_deliveries": rows(
                    connection,
                    """
                    SELECT d.id, d.notification_id, n.title AS notification_title,
                           d.channel, d.status, d.attempt,
                           d.last_error_code, d.next_retry_at, d.delivered_at,
                           d.created_at, d.updated_at
                    FROM notification_deliveries d
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE n.resource_type = 'WORK_ORDER' AND n.resource_id = :id
                    ORDER BY d.id
                    """,
                    {"id": args.work_order_id},
                ),
                "delivery_intents": rows(
                    connection,
                    """
                    SELECT i.id, i.operation, i.resource_id, i.resource_version,
                           i.generation, i.status, i.publish_attempt,
                           i.execution_attempt, i.last_error_code, i.created_at,
                           i.updated_at, i.finished_at
                    FROM task_intents i
                    JOIN notification_deliveries d ON d.id = i.resource_id
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE i.operation = 'DELIVER_NOTIFICATION'
                      AND n.resource_type = 'WORK_ORDER' AND n.resource_id = :id
                    ORDER BY i.id
                    """,
                    {"id": args.work_order_id},
                ),
                "protected_imported_work_orders": rows(
                    connection,
                    """
                    SELECT w.id, w.status, w.version, w.updated_at,
                           COUNT(l.id) AS log_count
                    FROM work_orders w
                    LEFT JOIN work_order_logs l ON l.work_order_id = w.id
                    WHERE w.id IN (42, 43, 44)
                    GROUP BY w.id, w.status, w.version, w.updated_at
                    ORDER BY w.id
                    """,
                    {},
                ),
            }
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "phase": args.phase,
        "work_order_id": args.work_order_id,
        "sla_state": evidence["sla_clock"][0]["state"],
        "logs": len(evidence["logs"]),
        "notifications": len(evidence["notifications"]),
        "delivery_intents": len(evidence["delivery_intents"]),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
