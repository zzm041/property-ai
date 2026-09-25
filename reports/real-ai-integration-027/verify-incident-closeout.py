"""Read back the page disposition, its audit, and unchanged Incident members."""
from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = Path(__file__).resolve().parent
PREP_PATH = REPORT_DIR / "prepare-incident-closeout.py"
PRIVATE_STATE = ROOT / "tmp/production-v1-closeout/incident-disposition/context.json"
PUBLIC = REPORT_DIR / "runtime-closeout/incident-database.json"


def _load_prep():
    spec = importlib.util.spec_from_file_location("incident_closeout_prep", PREP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Incident prerequisite helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = _load_prep()
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.model.ai import Incident, IncidentWorkOrder
from app.model.identity import AuditLog
from app.model.task_intent import TaskIntent
from app.model.work_order import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderFeedback,
    WorkOrderReceipt,
)
from app.core.database import create_database_engine


def _snapshot(session: Session, ids: list[int]) -> list[dict[str, object]]:
    rows = []
    for work_order_id in ids:
        work = session.get(WorkOrder, work_order_id)
        rows.append({
            "work_order_id": work_order_id,
            "status": None if work is None else work.status,
            "version": None if work is None else work.version,
            "current_assignment_id": None if work is None else work.current_assignment_id,
            "assignment_count": session.scalar(select(func.count()).select_from(WorkOrderAssignment)
                                                .where(WorkOrderAssignment.work_order_id == work_order_id)),
            "receipt_count": session.scalar(select(func.count()).select_from(WorkOrderReceipt)
                                             .where(WorkOrderReceipt.work_order_id == work_order_id)),
            "feedback_count": session.scalar(select(func.count()).select_from(WorkOrderFeedback)
                                              .where(WorkOrderFeedback.work_order_id == work_order_id)),
        })
    return rows


def main() -> None:
    if PUBLIC.exists():
        raise RuntimeError("final Incident evidence already exists; do not repeat API checks")
    state = json.loads(PRIVATE_STATE.read_text(encoding="utf-8"))
    source = json.loads(prep.PRIVATE_SOURCE.read_text(encoding="utf-8"))
    run_id = f"{state['run_id']}-verify"
    client = prep.Client(run_id)
    manager = client.login(str(source["phones"]["manager"]), "manager")
    incident_id = int(state["incident_id"])
    current = client.data("GET", f"/supervisor/incidents/{incident_id}",
                          token=manager, label="current-detail")
    if current["status"] != "MONITORING":
        raise RuntimeError("page disposition was not persisted")
    client.call("POST", f"/supervisor/incidents/{incident_id}/status",
                token=manager, label="stale-write",
                body={"status": "RESOLVED", "reason": "使用过期版本尝试处置",
                      "expected_updated_at": state["initial_updated_at"]}, expected=(409,))

    settings = prep._settings()
    engine = create_database_engine(settings.active_database_url)
    try:
        ids = [int(value) for value in state["work_order_ids"]]
        with Session(engine, expire_on_commit=False) as session:
            incident = session.get(Incident, incident_id)
            links = list(session.scalars(select(IncidentWorkOrder).where(
                IncidentWorkOrder.incident_id == incident_id).order_by(IncidentWorkOrder.work_order_id)))
            audit = session.scalar(select(AuditLog).where(
                AuditLog.resource_type == "INCIDENT",
                AuditLog.resource_id == incident_id,
                AuditLog.action == "INCIDENT_UPDATED",
                AuditLog.actor_user_id.is_not(None),
            ).order_by(AuditLog.id.desc()).limit(1))
            if incident is None or incident.status != "MONITORING":
                raise RuntimeError("database Incident status does not match the page")
            if {item.work_order_id for item in links} != set(ids):
                raise RuntimeError("Incident membership changed")
            if audit is None or audit.details_json != {
                "incident_id": incident_id,
                "category": incident.category,
                "work_order_count": len(ids),
                "from_status": "OPEN",
                "to_status": "MONITORING",
                "reason": "已核实公共区域风险并进入持续监控",
            }:
                raise RuntimeError("Incident disposition audit is missing or inaccurate")
            after = _snapshot(session, ids)
            protected_after = prep._member_snapshot(session, list(prep.PROTECTED_WORK_ORDERS))
            for before, item in zip(state["member_before"], after, strict=True):
                for key in ("work_order_id", "status", "version", "current_assignment_id", "assignment_count"):
                    if before[key] != item[key]:
                        raise RuntimeError(f"Incident disposition changed member {key}")
                if item["receipt_count"] != 0 or item["feedback_count"] != 0:
                    raise RuntimeError("Incident disposition fabricated receipt or feedback history")
            if protected_after != state["protected_before"]:
                raise RuntimeError("a protected historical work order changed")
            intents = list(session.scalars(select(TaskIntent).where(
                TaskIntent.id.in_([int(value) for value in state["intent_ids"]])
            ).order_by(TaskIntent.id)))
            intent_rows = [{"id": item.id, "operation": item.operation, "status": item.status,
                            "execution_attempt": item.execution_attempt} for item in intents]
            if len(intent_rows) != len(ids) or any(item["status"] != "SUCCEEDED" for item in intent_rows):
                raise RuntimeError("bounded Development analysis intents are not terminal-success")
            evidence = {
                "task": "Production V1 closeout Incident disposition runtime",
                "captured_at": datetime.now(UTC).isoformat(),
                "source_commit": "fe90913a6f3918bca33b2f100137a786a67c4f86",
                "environment": {"database": "AUTH001@0012", "api": "127.0.0.1:8083",
                                "page": "WeChat DevTools", "real_qwen_calls": 0},
                "incident": {"id": incident.id, "status": incident.status,
                             "work_order_count": incident.work_order_count,
                             "member_ids": [item.work_order_id for item in links]},
                "audit": {"action": audit.action, "actor_present": audit.actor_user_id is not None,
                          "details": audit.details_json, "request_id_present": bool(audit.request_id)},
                "member_after": after,
                "member_business_records_unchanged": True,
                "protected_work_orders_unchanged": True,
                "task_intents": intent_rows,
                "api_checks": client.calls,
                "stale_expected_updated_at": 409,
                "private_contact_or_tokens_in_public_evidence": False,
            }
            PUBLIC.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"incident_id": incident_id, "status": incident.status,
                              "members_unchanged": True, "audit": "verified"}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
