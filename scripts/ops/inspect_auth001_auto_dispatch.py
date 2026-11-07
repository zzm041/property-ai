"""Read-only, redacted evidence for one AUTH001 automatic dispatch run."""

from __future__ import annotations

import argparse
from datetime import UTC
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _rows(connection, statement: str, parameters: dict[str, object]) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(text(statement), parameters).mappings()]


def inspect_dispatch(work_order_id: int) -> dict[str, object]:
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        order = connection.execute(
            text(
                "SELECT wo.id, wo.tenant_id, wo.property_id, wo.status, wo.process_reason, "
                "wo.category, wo.priority, wo.current_assignment_id, wo.version, "
                "wo.created_at, p.timezone "
                "FROM work_orders wo JOIN properties p ON p.id = wo.property_id "
                "WHERE wo.id = :work_order_id"
            ),
            {"work_order_id": work_order_id},
        ).mappings().one()
        created_at = order["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        business_date = created_at.astimezone(ZoneInfo(order["timezone"])).date()
        assignment = connection.execute(
            text(
                "SELECT id, employee_id, assigned_by_user_id, assigned_source, "
                "status, reason, payload_json FROM work_order_assignments "
                "WHERE work_order_id = :work_order_id ORDER BY id DESC LIMIT 1"
            ),
            {"work_order_id": work_order_id},
        ).mappings().one_or_none()
        assignment_rows = _rows(
            connection,
            "SELECT id, employee_id, assigned_by_user_id, assigned_source, status, "
            "reason, ended_at, payload_json FROM work_order_assignments "
            "WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        payload = _json_value(None if assignment is None else assignment["payload_json"])
        if not isinstance(payload, dict):
            payload = {}
        snapshot = payload.get("filter_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        candidates = snapshot.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        analyses = _rows(
            connection,
            "SELECT id, status, category, priority, risk, required_skill, confidence "
            "FROM work_order_analyses WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        ai_columns = {item["name"] for item in inspect(connection).get_columns("ai_call_logs")}
        provider_mode = "provider_mode" if "provider_mode" in ai_columns else "NULL AS provider_mode"
        ai_calls = _rows(
            connection,
            f"SELECT provider_type, {provider_mode}, operation, model_version, status, prompt_version "
            "FROM ai_call_logs WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        recommendations = _rows(
            connection,
            "SELECT employee_id, eligible, filter_reason, score, reason_codes "
            "FROM dispatch_recommendations WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        logs = _rows(
            connection,
            "SELECT actor_type, actor_user_id IS NULL AS actor_is_system, event_code, "
            "from_status, to_status, request_id, created_at FROM work_order_logs "
            "WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        audits = _rows(
            connection,
            "SELECT action, resource_type, resource_id, request_id, "
            "actor_user_id IS NULL AS actor_is_system "
            "FROM audit_logs WHERE ("
            "(resource_type = 'WORK_ORDER' AND resource_id = :work_order_id) OR "
            "(resource_type = 'WORK_ORDER_ANALYSIS' AND resource_id IN ("
            "SELECT id FROM work_order_analyses WHERE work_order_id = :work_order_id"
            ")) OR "
            "(resource_type = 'WORK_ORDER_ASSIGNMENT' AND resource_id IN ("
            "SELECT id FROM work_order_assignments WHERE work_order_id = :work_order_id"
            "))) ORDER BY id",
            {"work_order_id": work_order_id},
        )
        notifications = _rows(
            connection,
            "SELECT n.id, n.user_id, n.resource_type, n.resource_id, "
            "d.status AS delivery_status "
            "FROM notifications n LEFT JOIN notification_deliveries d "
            "ON d.notification_id = n.id "
            "WHERE n.resource_type = 'WORK_ORDER' AND n.resource_id = :work_order_id "
            "ORDER BY n.id, d.id",
            {"work_order_id": work_order_id},
        )
        processes = _rows(
            connection,
            "SELECT id, cycle_no, assignee_id, started_at, ended_at "
            "FROM work_order_processes WHERE work_order_id = :work_order_id "
            "ORDER BY cycle_no",
            {"work_order_id": work_order_id},
        )
        receipts = _rows(
            connection,
            "SELECT id, process_id, problem_type, method, materials, fee_amount, "
            "fee_flag, resolved, unresolved_reason, follow_up, finished_at "
            "FROM work_order_receipts WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        feedbacks = _rows(
            connection,
            "SELECT id, process_id, actor_user_id, confirmed, unresolved_flag, rating, "
            "comment, created_at FROM work_order_feedbacks "
            "WHERE work_order_id = :work_order_id ORDER BY id",
            {"work_order_id": work_order_id},
        )
        identities = _rows(
            connection,
            "SELECT wo.reporter_user_id, a.employee_id, e.user_id AS employee_user_id "
            "FROM work_orders wo LEFT JOIN work_order_assignments a "
            "ON a.id = (SELECT MAX(latest.id) FROM work_order_assignments latest "
            "WHERE latest.work_order_id = wo.id) LEFT JOIN employees e "
            "ON e.id = a.employee_id WHERE wo.id = :work_order_id",
            {"work_order_id": work_order_id},
        )
        lifecycle_audits = _rows(
            connection,
            "SELECT action, actor_user_id, request_id FROM audit_logs "
            "WHERE resource_type = 'WORK_ORDER' AND resource_id = :work_order_id "
            "ORDER BY id",
            {"work_order_id": work_order_id},
        )
        daily = _rows(
            connection,
            "SELECT employee_id, business_date, on_duty, current_load "
            "FROM employee_daily_statuses WHERE property_id = :property_id "
            "AND business_date = :business_date AND employee_id IN ("
            "SELECT employee_id FROM dispatch_recommendations "
            "WHERE work_order_id = :work_order_id UNION "
            "SELECT employee_id FROM work_order_assignments "
            "WHERE work_order_id = :work_order_id) "
            "ORDER BY employee_id",
            {
                "property_id": order["property_id"],
                "business_date": business_date,
                "work_order_id": work_order_id,
            },
        )
        attendance = _rows(
            connection,
            "SELECT employee_id, business_date, check_in_at, check_out_at, "
            "check_out_at IS NULL AS currently_checked_in "
            "FROM attendance_records WHERE property_id = :property_id "
            "AND business_date = :business_date AND employee_id IN ("
            "SELECT employee_id FROM dispatch_recommendations "
            "WHERE work_order_id = :work_order_id UNION "
            "SELECT employee_id FROM work_order_assignments "
            "WHERE work_order_id = :work_order_id) "
            "ORDER BY employee_id",
            {
                "property_id": order["property_id"],
                "business_date": business_date,
                "work_order_id": work_order_id,
            },
        )
    engine.dispose()
    database_url = make_url(settings.test_database_url)
    assignment_history = []
    for item in assignment_rows:
        item_payload = _json_value(item.pop("payload_json"))
        item_payload = item_payload if isinstance(item_payload, dict) else {}
        item_snapshot = item_payload.get("filter_snapshot")
        item_snapshot = item_snapshot if isinstance(item_snapshot, dict) else {}
        item_candidates = item_snapshot.get("candidates")
        item_candidates = item_candidates if isinstance(item_candidates, list) else []
        assignment_history.append(
            item
            | {
                "assigned_by_user_id_is_null": item.pop("assigned_by_user_id") is None,
                "score": item_payload.get("score"),
                "candidate_filters": [
                    {
                        "employee_id": candidate.get("employee_id"),
                        "eligible": candidate.get("eligible"),
                        "filter_reason": candidate.get("filter_reason"),
                    }
                    for candidate in item_candidates
                    if isinstance(candidate, dict)
                ],
            }
        )
    return {
        "environment": {
            "database": f"{database_url.host}:{database_url.port}/{database_url.database}",
            "revision": revision,
            "queue": settings.celery_task_queue,
            "ai_auto_assign": settings.ai_auto_assign,
            "ai_enqueue_on_create": settings.ai_enqueue_on_create,
        },
        "work_order": {
            key: value for key, value in order.items() if key != "timezone"
        }
        | {"business_date": business_date},
        "assignment": None
        if assignment is None
        else {
            "id": assignment["id"],
            "employee_id": assignment["employee_id"],
            "assigned_by_user_id_is_null": assignment["assigned_by_user_id"] is None,
            "assigned_source": assignment["assigned_source"],
            "status": assignment["status"],
            "reason": assignment["reason"],
            "score": payload.get("score"),
            "required_duty": payload.get("required_duty"),
            "required_skill": payload.get("required_skill"),
            "filter_candidate_count": len(candidates),
            "eligible_employee_ids": [
                item.get("employee_id")
                for item in candidates
                if isinstance(item, dict) and item.get("eligible") is True
            ],
            "weights": snapshot.get("weights"),
        },
        "assignment_history": assignment_history,
        "analyses": analyses,
        "ai_calls": ai_calls,
        "dispatch_recommendations": recommendations,
        "work_order_logs": logs,
        "audits": audits,
        "notifications": notifications,
        "processes": processes,
        "receipts": receipts,
        "resident_feedbacks": feedbacks,
        "lifecycle_identities": identities,
        "lifecycle_audits": lifecycle_audits,
        "employee_daily_statuses": daily,
        "attendance_records": attendance,
        "redaction": "No phone, token, credential, resident name, or work-order text is emitted.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-order-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.work_order_id <= 0:
        parser.error("--work-order-id must be positive")
    serialized = json.dumps(
        inspect_dispatch(args.work_order_id),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if args.output is not None:
        output = args.output.resolve()
        reports = (REPO / "reports").resolve()
        if reports not in output.parents:
            parser.error("--output must be under reports/")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
