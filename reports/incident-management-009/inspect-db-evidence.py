"""Read-only, redacted database evidence for INCIDENT-MGMT-UI-009."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url

from auth001_isolated_runtime import build_isolated_settings


REPO = Path(__file__).resolve().parents[2]
PRIVATE_RUN = REPO / "tmp" / "incident-management-009" / "context.json"
PAGE_EVIDENCE = Path(__file__).resolve().parent / "page-evidence.json"
OUTPUT = Path(__file__).resolve().parent / "database-evidence.json"
CODE_SHA = "9033caae493f120789f07f919d69666afc33625b"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _row_dicts(rows) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


def main() -> None:
    private = json.loads(PRIVATE_RUN.read_text(encoding="utf-8"))
    page = json.loads(PAGE_EVIDENCE.read_text(encoding="utf-8"))
    incident_id = int(private["incident_id"])
    work_order_ids = sorted(int(value) for value in private["work_order_ids"])
    announcement_id = int(page["objects"]["announcement_draft_id"])
    manager_user_id = int(private["sessions"]["manager"]["user_id"])
    staff_user_id = int(private["sessions"]["staff"]["user_id"])
    employee_id = int(private["employee_id"])

    settings = build_isolated_settings()
    target = make_url(settings.test_database_url)
    if (
        settings.app_env != "test"
        or target.host != "127.0.0.1"
        or target.port != 3307
        or target.database != "property_ai_auth001_test"
    ):
        raise RuntimeError("Database evidence reader refused a non-AUTH001 target")

    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != "20260919_0009":
                raise RuntimeError(f"Unexpected AUTH001 revision: {revision}")

            incident = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, building_id, status, "
                        "category, subcategory, primary_work_order_id, "
                        "primary_employee_id, work_order_count, "
                        "summary IS NOT NULL AS summary_present, "
                        "ai_summary IS NOT NULL AS ai_summary_present, "
                        "affected_scope IS NOT NULL AS affected_scope_present, "
                        "created_at, updated_at "
                        "FROM incidents WHERE id=:incident_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings().one()
            )
            links = _row_dicts(
                connection.execute(
                    text(
                        "SELECT incident_id, work_order_id, tenant_id, property_id "
                        "FROM incident_work_orders WHERE incident_id=:incident_id "
                        "ORDER BY work_order_id"
                    ),
                    {"incident_id": incident_id},
                ).mappings()
            )
            orders = _row_dicts(
                connection.execute(
                    text(
                        "SELECT w.id, w.status, w.incident_id, w.current_assignment_id, "
                        "a.employee_id, a.assigned_by_user_id, a.status AS assignment_status, "
                        "a.reason, a.assigned_source, a.ended_at "
                        "FROM work_orders w "
                        "JOIN work_order_assignments a ON a.id=w.current_assignment_id "
                        "WHERE w.id IN :work_order_ids ORDER BY w.id"
                    ).bindparams(bindparam("work_order_ids", expanding=True)),
                    {"work_order_ids": work_order_ids},
                ).mappings()
            )
            analyses = _row_dicts(
                connection.execute(
                    text(
                        "SELECT work_order_id, status, category, subcategory, "
                        "required_skill, prompt_version, error_code "
                        "FROM work_order_analyses WHERE work_order_id IN :work_order_ids "
                        "ORDER BY work_order_id, analysis_version"
                    ).bindparams(bindparam("work_order_ids", expanding=True)),
                    {"work_order_ids": work_order_ids},
                ).mappings()
            )
            providers = _row_dicts(
                connection.execute(
                    text(
                        "SELECT work_order_id, provider_type, operation, model_version, "
                        "status, error_code FROM ai_call_logs "
                        "WHERE work_order_id IN :work_order_ids ORDER BY work_order_id, id"
                    ).bindparams(bindparam("work_order_ids", expanding=True)),
                    {"work_order_ids": work_order_ids},
                ).mappings()
            )
            participant_user_ids = [
                int(value)
                for value in connection.execute(
                    text(
                        "SELECT DISTINCT e.user_id "
                        "FROM incident_work_orders iwo "
                        "JOIN work_orders w ON w.id=iwo.work_order_id "
                        "JOIN work_order_assignments a ON a.id=w.current_assignment_id "
                        "JOIN employees e ON e.id=a.employee_id "
                        "WHERE iwo.incident_id=:incident_id "
                        "AND a.status='ACTIVE' AND a.ended_at IS NULL "
                        "ORDER BY e.user_id"
                    ),
                    {"incident_id": incident_id},
                ).scalars()
            ]
            announcement = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, incident_id, status, ai_draft, "
                        "created_by_user_id, reviewed_by_user_id, published_by_user_id, "
                        "published_at, created_at, updated_at "
                        "FROM announcements WHERE id=:announcement_id"
                    ),
                    {"announcement_id": announcement_id},
                ).mappings().one()
            )
            announcement_scopes = _row_dicts(
                connection.execute(
                    text(
                        "SELECT scope_type, building_id, unit_id, audience "
                        "FROM announcement_scopes "
                        "WHERE announcement_id=:announcement_id ORDER BY id"
                    ),
                    {"announcement_id": announcement_id},
                ).mappings()
            )
            announcement_review_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM announcement_reviews "
                        "WHERE announcement_id=:announcement_id"
                    ),
                    {"announcement_id": announcement_id},
                ).scalar_one()
            )
            audits = _row_dicts(
                connection.execute(
                    text(
                        "SELECT action, resource_type, resource_id, actor_user_id, "
                        "request_id, created_at FROM audit_logs "
                        "WHERE (resource_type='INCIDENT' AND resource_id=:incident_id) "
                        "OR (resource_type='ANNOUNCEMENT' AND resource_id=:announcement_id) "
                        "ORDER BY id"
                    ),
                    {
                        "incident_id": incident_id,
                        "announcement_id": announcement_id,
                    },
                ).mappings()
            )
    finally:
        engine.dispose()

    linked_ids = sorted(int(item["work_order_id"]) for item in links)
    if linked_ids != work_order_ids or int(incident["work_order_count"]) != len(work_order_ids):
        raise RuntimeError("Incident membership did not match the page/API work orders")
    if (
        len(orders) != len(work_order_ids)
        or any(item["status"] != "ASSIGNED" for item in orders)
        or any(item["assigned_source"] != "AUTO" for item in orders)
        or any(item["assigned_by_user_id"] is not None for item in orders)
        or any(int(item["employee_id"]) != employee_id for item in orders)
        or any(item["assignment_status"] != "ACTIVE" for item in orders)
        or any(item["ended_at"] is not None for item in orders)
    ):
        raise RuntimeError("Work-order automatic assignment evidence was inconsistent")
    if (
        len(analyses) != len(work_order_ids)
        or any(item["status"] != "COMPLETED" for item in analyses)
        or any(item["category"] != "ELECTRICAL" for item in analyses)
    ):
        raise RuntimeError("Development analysis evidence was incomplete")
    if participant_user_ids != [staff_user_id]:
        raise RuntimeError("Current Incident participant identity was inconsistent")
    if (
        int(incident["primary_employee_id"]) != employee_id
        or not bool(incident["ai_summary_present"])
    ):
        raise RuntimeError("Incident summary or primary employee was not persisted")
    if (
        int(announcement["incident_id"]) != incident_id
        or announcement["status"] != "DRAFT"
        or int(announcement["ai_draft"]) != 1
        or int(announcement["created_by_user_id"]) != manager_user_id
        or announcement["reviewed_by_user_id"] is not None
        or announcement["published_by_user_id"] is not None
        or announcement["published_at"] is not None
        or announcement_review_count != 0
    ):
        raise RuntimeError("Announcement draft was not preserved as an unpublished draft")

    audit_rows = [
        {
            **{key: value for key, value in item.items() if key != "request_id"},
            "request_id_hash": _hash(str(item["request_id"])),
        }
        for item in audits
    ]
    audit_actions = [str(item["action"]) for item in audit_rows]
    if "INCIDENT_UPDATED" not in audit_actions or "ANNOUNCEMENT_DRAFT_CREATED" not in audit_actions:
        raise RuntimeError("Expected Incident and announcement audit actions were missing")

    evidence = {
        "task_id": "INCIDENT-MGMT-UI-009",
        "phase": "read_only_database",
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": CODE_SHA,
        "run_id": private["run_id"],
        "target": {
            "host": target.host,
            "port": target.port,
            "database": target.database,
            "revision": revision,
            "app_env": settings.app_env,
        },
        "incident": incident,
        "incident_work_orders": links,
        "work_orders_and_current_assignments": orders,
        "analyses": analyses,
        "provider_calls": providers,
        "current_participant_user_ids": participant_user_ids,
        "announcement": announcement,
        "announcement_scopes": announcement_scopes,
        "announcement_review_count": announcement_review_count,
        "audit_rows": audit_rows,
        "checks": {
            "same_incident_and_five_work_orders": True,
            "all_current_assignments_are_auto": True,
            "all_current_assignments_match_staff_employee": True,
            "development_analyses_completed": True,
            "current_participant_matches_staff": True,
            "ai_summary_persisted": True,
            "announcement_remains_unreviewed_draft": True,
            "incident_and_draft_audited": True,
        },
        "boundaries": {
            "transaction_mode": "READ ONLY",
            "business_state_written": False,
            "development_provider_only": True,
            "real_qwen_or_production_ai_claimed": False,
        },
        "redaction": "No phone, token, OTP, password, resident text, or raw request ID is emitted.",
    }
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["checks"], indent=2))


if __name__ == "__main__":
    main()
