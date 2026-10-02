"""Collect read-only DB evidence for the page-submitted 0014 identity/SLA run."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
FIXED_BACKEND = Path(os.environ["AUTH001_FIXED_BACKEND"]).resolve()
for candidate in (FIXED_BACKEND, ROOT / "scripts" / "ops"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
import site

site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))

from sqlalchemy import text

from app.core.database import create_database_engine
from auth001_retry_runtime import apply_environ, build_settings

PAGE_EVIDENCE = Path(os.environ.get(
    "AUTH001_PAGE_EVIDENCE",
    Path(__file__).resolve().parent / "page-submit-evidence-2.json",
))
OUTPUT = Path(os.environ.get(
    "AUTH001_DB_EVIDENCE",
    Path(__file__).resolve().parent / "page-submit-db-evidence-2.json",
))


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError("read-only evidence output already exists")
    page = json.loads(PAGE_EVIDENCE.read_text(encoding="utf-8"))
    identity_phase = page["phases"].get("current_clock_finalize_and_complete")
    if identity_phase is None:
        identity_page = json.loads(
            (Path(__file__).resolve().parent / "page-submit-evidence-2.json").read_text(encoding="utf-8")
        )
        if identity_page.get("run_id") != page.get("run_id"):
            raise RuntimeError("SLA evidence does not match the completed identity run")
        identity_phase = identity_page["phases"]["current_clock_finalize_and_complete"]
    case_id = int(identity_phase["case_id"])
    policy_id = int(page["phases"]["sla_policy_and_work_order_submit"]["policy_id"])
    order_id = int(page["phases"]["sla_policy_and_work_order_submit"]["work_order_id"])

    settings = build_settings(enable_enqueue=False, enable_test_proof=True)
    apply_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            identity = connection.execute(text(
                "SELECT c.status,c.version,c.source_user_id,c.target_user_id,"
                "COUNT(DISTINCT r.id),COUNT(DISTINCT m.id),MAX(m.status),"
                "(SELECT COUNT(*) FROM auth_sessions s WHERE s.user_id=c.source_user_id AND s.revoked_at IS NULL),"
                "(SELECT COUNT(*) FROM user_passwords p WHERE p.user_id=c.source_user_id),"
                "(SELECT COUNT(*) FROM audit_logs a WHERE a.resource_type='IDENTITY_RECOVERY_CASE' AND a.resource_id=c.id) "
                "FROM identity_recovery_cases c "
                "LEFT JOIN identity_recovery_reviews r ON r.case_id=c.id AND r.decision='APPROVE' "
                "LEFT JOIN account_merges m ON m.recovery_case_id=c.id "
                "WHERE c.id=:id GROUP BY c.id"
            ), {"id": case_id}).one()
            users = connection.execute(text(
                "SELECT id,status FROM users WHERE id IN (:source,:target) ORDER BY id"
            ), {"source": int(identity[2]), "target": int(identity[3])}).all()
            sla = connection.execute(text(
                "SELECT a.policy_version_id,a.source,a.accept_minutes,a.respond_minutes,a.complete_minutes,"
                "a.warning_lead_minutes,a.escalation_minutes,w.status,w.contact_phone_e164 "
                "FROM work_order_sla_applications a JOIN work_orders w ON w.id=a.work_order_id "
                "WHERE a.work_order_id=:id"
            ), {"id": order_id}).one()
            versions = connection.execute(text(
                "SELECT version_no,accept_minutes,respond_minutes,complete_minutes,warning_lead_minutes,"
                "escalation_minutes,status FROM sla_policy_versions WHERE policy_id=:id ORDER BY version_no"
            ), {"id": policy_id}).all()
            clock = connection.execute(text(
                "SELECT state,accept_due_at,respond_due_at,complete_due_at,warning_at "
                "FROM sla_clocks WHERE work_order_id=:id"
            ), {"id": order_id}).one()
            task_intents = connection.execute(text(
                "SELECT COUNT(*) FROM task_intents WHERE operation='ANALYZE_WORK_ORDER' AND resource_id=:id"
            ), {"id": order_id}).scalar_one()
    finally:
        engine.dispose()

    result = {
        "task_id": "PRODUCTION-V1-3317-IDENTITY-SLA-PAGE-SUBMIT",
        "run_id": page["run_id"],
        "collected_utc": datetime.now(UTC).isoformat(),
        "mode": "read-only SQL after normal page submissions",
        "environment": "127.0.0.1:3317/property_ai_auth001_test",
        "revision": revision,
        "identity": {
            "case_id": case_id,
            "status": identity[0],
            "version": int(identity[1]),
            "source_user_id": int(identity[2]),
            "target_user_id": int(identity[3]),
            "approve_review_count": int(identity[4]),
            "merge_rows": int(identity[5]),
            "merge_status": identity[6],
            "source_active_sessions": int(identity[7]),
            "source_password_rows": int(identity[8]),
            "audit_rows": int(identity[9]),
            "user_statuses": [{"user_id": int(row[0]), "status": row[1]} for row in users],
        },
        "sla": {
            "policy_id": policy_id,
            "versions": [
                {
                    "version_no": int(row[0]), "accept_minutes": int(row[1]),
                    "respond_minutes": int(row[2]), "complete_minutes": int(row[3]),
                    "warning_lead_minutes": int(row[4]), "escalation_minutes": int(row[5]),
                    "status": row[6],
                }
                for row in versions
            ],
            "work_order_id": order_id,
            "application": {
                "policy_version_id": int(sla[0]) if sla[0] is not None else None, "source": sla[1],
                "accept_minutes": int(sla[2]), "respond_minutes": int(sla[3]),
                "complete_minutes": int(sla[4]), "warning_lead_minutes": int(sla[5]),
                "escalation_minutes": int(sla[6]),
            },
            "work_order_status": sla[7],
            "contact_phone_present": bool(sla[8]),
            "clock": {
                "state": clock[0],
                "all_deadlines_present": all(value is not None for value in clock[1:]),
            },
            "task_intent_count": int(task_intents),
        },
        "sensitive_values_in_output": False,
        "business_sql_writes": 0,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "work_order_id": order_id, "identity": identity[0], "sla_versions": len(versions)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
