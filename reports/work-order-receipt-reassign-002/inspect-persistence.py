"""Read-only, redacted persistence inspection for WO-RECEIPT-REASSIGN-002."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, text


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.provider.storage import DevelopmentStorageProvider  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


OUTPUT = Path(__file__).resolve().parent / "persistence-evidence.json"
WORK_ORDER_ID = 10


def _rows(connection, statement: str, parameters: dict[str, object]):
    return [
        dict(row)
        for row in connection.execute(text(statement), parameters).mappings()
    ]


def main() -> int:
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        work_order = _rows(
            connection,
            "SELECT id, tenant_id, property_id, status, process_reason, "
            "current_assignment_id, version FROM work_orders WHERE id = :id",
            {"id": WORK_ORDER_ID},
        )[0]
        assignments = _rows(
            connection,
            "SELECT id, employee_id, assigned_by_user_id, assigned_source, status, "
            "reason, assigned_at, accept_at, ended_at FROM work_order_assignments "
            "WHERE work_order_id = :id ORDER BY id",
            {"id": WORK_ORDER_ID},
        )
        processes = _rows(
            connection,
            "SELECT id, cycle_no, assignee_id, started_at, ended_at "
            "FROM work_order_processes WHERE work_order_id = :id ORDER BY cycle_no",
            {"id": WORK_ORDER_ID},
        )
        receipts = _rows(
            connection,
            "SELECT id, process_id, problem_type, method, materials, fee_flag, "
            "resolved, unresolved_reason, follow_up, finished_at "
            "FROM work_order_receipts WHERE work_order_id = :id ORDER BY id",
            {"id": WORK_ORDER_ID},
        )
        media = _rows(
            connection,
            "SELECT m.id, m.file_id, m.kind, m.taken_phase, m.uploader_user_id, "
            "f.mime_type, f.size_bytes, f.content_hash, f.storage_key "
            "FROM work_order_media m JOIN file_objects f ON f.id = m.file_id "
            "AND f.tenant_id = m.tenant_id WHERE m.work_order_id = :id ORDER BY m.id",
            {"id": WORK_ORDER_ID},
        )
        logs = _rows(
            connection,
            "SELECT actor_user_id, actor_type, from_status, to_status, process_reason, "
            "event_code, request_id, created_at FROM work_order_logs "
            "WHERE work_order_id = :id ORDER BY id",
            {"id": WORK_ORDER_ID},
        )
        audits = _rows(
            connection,
            "SELECT action, actor_user_id, resource_type, resource_id, request_id "
            "FROM audit_logs WHERE tenant_id = :tenant_id AND property_id = :property_id "
            "AND ((resource_type = 'WORK_ORDER' AND resource_id = :id) OR "
            "(resource_type = 'WORK_ORDER_ASSIGNMENT' AND resource_id IN "
            "(SELECT id FROM work_order_assignments WHERE work_order_id = :id))) "
            "ORDER BY id",
            {
                "id": WORK_ORDER_ID,
                "tenant_id": work_order["tenant_id"],
                "property_id": work_order["property_id"],
            },
        )
        target_identity = _rows(
            connection,
            "SELECT e.id AS employee_id, e.user_id FROM employees e WHERE e.id = 9",
            {},
        )[0]
    engine.dispose()

    storage = DevelopmentStorageProvider(persist_dir=settings.dev_storage_dir or None)
    redacted_media = []
    for item in media:
        content = storage.get_object(str(item.pop("storage_key")))
        expected_hash = str(item.pop("content_hash"))
        redacted_media.append(
            {
                **item,
                "object_exists": True,
                "size_matches": len(content) == item["size_bytes"],
                "hash_matches": sha256(content).hexdigest() == expected_hash,
            }
        )

    evidence = {
        "task_id": "WO-RECEIPT-REASSIGN-002",
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "storage": "isolated persistent development storage",
        },
        "work_order": work_order,
        "assignments": assignments,
        "processes": processes,
        "receipts": receipts,
        "media": redacted_media,
        "target_identity": target_identity,
        "logs": logs,
        "audits": audits,
        "checks": {
            "wait_confirm": work_order["status"] == "WAIT_CONFIRM",
            "old_assignment_reassigned": any(
                row["employee_id"] == 10 and row["status"] == "REASSIGNED"
                for row in assignments
            ),
            "target_assignment_active": any(
                row["employee_id"] == 9 and row["status"] == "ACTIVE"
                for row in assignments
            ),
            "process_assignee_history": [row["assignee_id"] for row in processes]
            == [10, 9],
            "resolved_receipt_for_target_cycle": bool(receipts)
            and bool(receipts[-1]["resolved"])
            and receipts[-1]["process_id"] == processes[-1]["id"],
            "before_and_after_media": {row["taken_phase"] for row in redacted_media}
            == {"BEFORE", "AFTER"},
            "media_uploader_is_target_user": all(
                row["uploader_user_id"] == target_identity["user_id"]
                for row in redacted_media
            ),
            "media_objects_integral": all(
                row["object_exists"] and row["size_matches"] and row["hash_matches"]
                for row in redacted_media
            ),
            "reassign_audited": any(
                row["action"] == "WORK_ORDER_REASSIGNED" for row in audits
            ),
            "receipt_audited": any(
                row["action"] == "WORK_ORDER_RESOLVED" for row in audits
            ),
        },
        "boundaries": {
            "read_only": True,
            "business_state_changed": False,
            "storage_objects_read_only": True,
            "credentials_emitted": False,
        },
        "redaction": (
            "Storage keys, phone numbers, tokens, passwords, names, work-order text, "
            "and credentials are omitted."
        ),
    }
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
