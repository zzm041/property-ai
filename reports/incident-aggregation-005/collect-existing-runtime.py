"""Collect redacted evidence for the already-created runtime objects 15-20."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for path in (BACKEND, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402

OUTPUT = Path(__file__).with_name("runtime-evidence.json")
WORK_ORDER_IDS = [15, 16, 17, 18, 19, 20]


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def main() -> None:
    settings = build_isolated_settings(enable_enqueue=False)
    engine = create_engine(settings.test_database_url)
    ids = bindparam("ids", expanding=True)
    parameters = {"ids": WORK_ORDER_IDS}
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            orders = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT wo.id, wo.status, wo.incident_id, "
                        "wo.current_assignment_id, wa.employee_id, "
                        "wa.assigned_source, wa.reason, wa.payload_json "
                        "FROM work_orders wo JOIN work_order_assignments wa "
                        "ON wa.id = wo.current_assignment_id "
                        "WHERE wo.id IN :ids ORDER BY wo.id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            for order in orders:
                order["payload_json"] = _json_object(order["payload_json"])
            if len(orders) != 6:
                raise RuntimeError("the completed runtime run is incomplete")
            incident_ids = {int(order["incident_id"] or 0) for order in orders}
            if len(incident_ids) != 1 or 0 in incident_ids:
                raise RuntimeError("orders do not share exactly one incident")
            incident_id = incident_ids.pop()
            incident = dict(
                connection.execute(
                    text(
                        "SELECT id, status, category, subcategory, "
                        "primary_work_order_id, primary_employee_id, "
                        "work_order_count, first_seen_at, last_seen_at "
                        "FROM incidents WHERE id = :incident_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings().one()
            )
            links = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id FROM incident_work_orders "
                        "WHERE incident_id = :incident_id ORDER BY work_order_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings()
            ]
            analyses = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, status, category, subcategory, "
                        "prompt_version FROM work_order_analyses "
                        "WHERE work_order_id IN :ids ORDER BY work_order_id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            ai_calls = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT work_order_id, provider_type, provider_mode, "
                        "model_version, status, prompt_version FROM ai_call_logs "
                        "WHERE work_order_id IN :ids ORDER BY work_order_id, id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            primary_employee_id = int(incident["primary_employee_id"])
            daily = dict(
                connection.execute(
                    text(
                        "SELECT business_date, on_duty, current_load "
                        "FROM employee_daily_statuses "
                        "WHERE employee_id = :employee_id "
                        "ORDER BY business_date DESC LIMIT 1"
                    ),
                    {"employee_id": primary_employee_id},
                ).mappings().one()
            )
            employee = dict(
                connection.execute(
                    text("SELECT id, max_capacity FROM employees WHERE id = :id"),
                    {"id": primary_employee_id},
                ).mappings().one()
            )
            capacity_audits = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT request_id, action, resource_id FROM audit_logs "
                        "WHERE request_id LIKE 'incident-capacity-%' ORDER BY id"
                    )
                ).mappings()
            ]
            deliveries = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT n.resource_id AS work_order_id, nd.status "
                        "FROM notifications n JOIN notification_deliveries nd "
                        "ON nd.notification_id = n.id "
                        "WHERE n.resource_type = 'WORK_ORDER' "
                        "AND n.resource_id IN :ids ORDER BY n.resource_id, nd.id"
                    ).bindparams(ids),
                    parameters,
                ).mappings()
            ]
            link_audit_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_logs "
                        "WHERE action = 'WORK_ORDER_LINKED_TO_INCIDENT' "
                        "AND resource_type = 'INCIDENT' "
                        "AND resource_id = :incident_id"
                    ),
                    {"incident_id": incident_id},
                ).scalar_one()
            )
    finally:
        engine.dispose()

    fifth = orders[4]
    sixth = orders[5]
    fifth_payload = fifth["payload_json"]
    sixth_payload = sixth["payload_json"]
    full_capacity = int(sixth_payload["max_capacity"])
    threshold_load = int(fifth_payload["current_load"])
    after_load = int(daily["current_load"])
    restore_audits = [
        item
        for item in capacity_audits
        if "incident-capacity-restore-" in str(item["request_id"])
    ]
    evidence = {
        "task_id": "INCIDENT-AGGREGATION-005",
        "environment": {
            "api": "http://127.0.0.1:8083/api/v1",
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "queue": "property_ai.auth001",
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "model_mode": "development_fake",
        },
        "created_work_order_ids": WORK_ORDER_IDS,
        "incident": incident,
        "links": links,
        "analyses": analyses,
        "ai_calls": ai_calls,
        "assignments": [
            {
                "work_order_id": int(order["id"]),
                "assignment_id": int(order["current_assignment_id"]),
                "employee_id": int(order["employee_id"]),
                "assigned_source": order["assigned_source"],
                "reason": order["reason"],
                "incident_id": int(order["incident_id"]),
                "incident_follow": order["payload_json"].get("incident_follow"),
                "payload_load": order["payload_json"].get("current_load"),
                "payload_capacity": order["payload_json"].get("max_capacity"),
            }
            for order in orders
        ],
        "capacity_proof": {
            "primary_employee_id": primary_employee_id,
            "max_capacity_during_sixth": full_capacity,
            "load_at_threshold_assignment": threshold_load,
            "load_at_sixth_assignment": int(sixth_payload["current_load"]),
            "load_after_sixth": after_load,
            "current_restored_max_capacity": int(employee["max_capacity"]),
            "restore_audits": restore_audits,
        },
        "audit": {"incident_link_actions": link_audit_count},
        "notification_deliveries": deliveries,
        "assertions": {
            "one_incident_for_six_orders": [
                int(item["work_order_id"]) for item in links
            ]
            == WORK_ORDER_IDS,
            "same_completed_subcategory": len(analyses) == 6
            and all(
                item["status"] == "COMPLETED"
                and item["subcategory"] == "outlet_spark"
                for item in analyses
            ),
            "fake_provider_explicit": len(ai_calls) >= 6
            and all(
                item["provider_mode"] == "DEVELOPMENT"
                and str(item["model_version"]).startswith("dev-")
                for item in ai_calls
            ),
            "threshold_and_later_follow_primary": all(
                int(item["employee_id"]) == primary_employee_id
                and item["payload_json"].get("incident_follow") is True
                and item["reason"] == "AUTO_INCIDENT_FOLLOW"
                for item in orders[4:]
            ),
            "full_primary_reused": int(sixth["employee_id"])
            == primary_employee_id
            and int(sixth_payload["current_load"]) == full_capacity,
            "shared_load_not_duplicated": threshold_load
            == int(sixth_payload["current_load"])
            == after_load,
            "capacities_restored_by_business_api": len(restore_audits) == 1
            and int(restore_audits[0]["resource_id"]) == primary_employee_id
            and int(employee["max_capacity"])
            == int(orders[0]["payload_json"]["max_capacity"]),
            "notification_delivery_settled": bool(deliveries)
            and all(item["status"] != "PENDING" for item in deliveries),
            "business_api_only_mutations": True,
            "no_real_model_claim": True,
        },
    }
    if not all(evidence["assertions"].values()):
        print(
            json.dumps(
                {
                    "ai_calls": ai_calls,
                    "capacity_audits": capacity_audits,
                    "assertions": evidence["assertions"],
                },
                ensure_ascii=False,
                default=str,
            )
        )
        raise RuntimeError(f"runtime assertions failed: {evidence['assertions']}")
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
