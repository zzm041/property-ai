"""Collect read-only AUTH001 database evidence for SLA-POLICY-MGMT-010."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from sqlalchemy import bindparam, text
from sqlalchemy.engine import make_url


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import create_database_engine  # noqa: E402
from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "SLA-POLICY-MGMT-010"
PRIVATE_STATE = REPO / "tmp" / "sla-policy-management-010" / "context.json"
PUBLIC_EVIDENCE = Path(__file__).resolve().parent / "database-evidence.json"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _guard(settings) -> None:
    database = make_url(settings.active_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = (
        database.host,
        database.port,
        database.database,
        settings.app_env,
        settings.celery_task_queue,
        broker.path.lstrip("/"),
    )
    expected = (
        "127.0.0.1",
        3307,
        "property_ai_auth001_test",
        "test",
        ISOLATED_QUEUE,
        ISOLATED_VHOST,
    )
    if actual != expected:
        raise RuntimeError(f"AUTH001 target guard rejected settings: {actual}")


def main() -> int:
    state = json.loads(PRIVATE_STATE.read_text(encoding="utf-8"))
    if state.get("stage") != "complete":
        raise RuntimeError("API runtime evidence is incomplete")
    settings = build_isolated_settings(enable_enqueue=True)
    _guard(settings)
    engine = create_database_engine(settings.active_database_url)

    tenant_id = int(state["org"]["tenant_id"])
    property_id = int(state["org"]["property_id"])
    work_order_id = int(state["work_order_id"])
    manager_user_id = int(state["sessions"]["manager"]["user_id"])
    property_admin_user_id = int(state["property_admin_user_id"])
    api_request_ids = dict(state["request_ids"])
    for item in state["concurrent"]:
        api_request_ids[str(item["label"])] = str(item["request_id"])
    api_request_ids["fixture-property-admin-grant"] = str(
        state["fixture_grant_request_id"]
    )
    inspected_labels = (
        "tenant-default-create",
        "property-override-create",
        "property-override-same-value-replay",
        "property-override-first-update",
        "property-override-stale-update",
        "concurrent-1",
        "concurrent-2",
        "policy-change-after-work-order",
        "fixture-property-admin-grant",
    )
    inspected_ids = [api_request_ids[label] for label in inspected_labels]

    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            database_name = connection.execute(text("SELECT DATABASE()")) .scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            role_rows = connection.execute(
                text(
                    "SELECT p.id AS permission_id, p.code AS permission_code, "
                    "r.code AS role_code, rp.id AS role_permission_id "
                    "FROM permissions AS p "
                    "LEFT JOIN role_permissions AS rp ON rp.permission_id = p.id "
                    "LEFT JOIN roles AS r ON r.id = rp.role_id "
                    "WHERE p.code = 'sla:manage' ORDER BY r.code"
                )
            ).mappings().all()
            user_grants = connection.execute(
                text(
                    "SELECT ur.user_id, r.code AS role_code, ur.scope_type, "
                    "ur.tenant_id, ur.property_id "
                    "FROM user_roles AS ur "
                    "JOIN roles AS r ON r.id = ur.role_id "
                    "JOIN role_permissions AS rp ON rp.role_id = r.id "
                    "JOIN permissions AS p ON p.id = rp.permission_id "
                    "WHERE ur.user_id IN :user_ids AND p.code = 'sla:manage' "
                    "ORDER BY ur.user_id, r.code"
                ).bindparams(bindparam("user_ids", expanding=True)),
                {"user_ids": [manager_user_id, property_admin_user_id]},
            ).mappings().all()
            policy_rows = connection.execute(
                text(
                    "SELECT id, tenant_id, property_id, category, priority, "
                    "respond_minutes, complete_minutes, status, updated_at "
                    "FROM sla_policies WHERE id IN :policy_ids ORDER BY id"
                ).bindparams(bindparam("policy_ids", expanding=True)),
                {
                    "policy_ids": [
                        int(state["tenant_policy"]["id"]),
                        int(state["property_policy_final"]["id"]),
                    ]
                },
            ).mappings().all()
            audit_rows = connection.execute(
                text(
                    "SELECT id, action, resource_type, actor_user_id, resource_id, "
                    "tenant_id, property_id, request_id, details_json, created_at "
                    "FROM audit_logs WHERE request_id IN :request_ids ORDER BY id"
                ).bindparams(bindparam("request_ids", expanding=True)),
                {"request_ids": inspected_ids},
            ).mappings().all()
            order_row = connection.execute(
                text(
                    "SELECT wo.id, wo.tenant_id, wo.property_id, wo.house_id, "
                    "wo.status, wo.category, wo.priority, wo.sla_policy_id, "
                    "wo.created_at, wo.assigned_at, wo.sla_due_at, "
                    "wo.current_assignment_id, wa.assigned_at AS assignment_created_at, "
                    "wa.assigned_by_user_id, wa.reason AS assignment_reason, "
                    "sc.accept_due_at, sc.respond_due_at, sc.complete_due_at, "
                    "sc.warning_at, sc.state AS clock_state "
                    "FROM work_orders AS wo "
                    "JOIN sla_clocks AS sc ON sc.work_order_id = wo.id "
                    "LEFT JOIN work_order_assignments AS wa "
                    "ON wa.id = wo.current_assignment_id "
                    "WHERE wo.id = :work_order_id"
                ),
                {"work_order_id": work_order_id},
            ).mappings().one()
            connection.rollback()
    finally:
        engine.dispose()

    if database_name != "property_ai_auth001_test" or revision != "20260919_0009":
        raise RuntimeError("Read-only database evidence target or revision changed")
    role_codes = [str(row["role_code"]) for row in role_rows]
    if len({int(row["permission_id"]) for row in role_rows}) != 1:
        raise RuntimeError("sla:manage permission is missing or duplicated")
    if role_codes != ["PROPERTY_ADMIN", "TENANT_ADMIN"]:
        raise RuntimeError(f"Unexpected sla:manage role mapping: {role_codes}")

    grant_shapes = {
        (
            int(row["user_id"]),
            str(row["role_code"]),
            str(row["scope_type"]),
            row["tenant_id"],
            row["property_id"],
        )
        for row in user_grants
    }
    expected_grants = {
        (manager_user_id, "TENANT_ADMIN", "TENANT", tenant_id, None),
        (
            property_admin_user_id,
            "PROPERTY_ADMIN",
            "PROPERTY",
            tenant_id,
            property_id,
        ),
    }
    if grant_shapes != expected_grants:
        raise RuntimeError(f"Runtime SLA grants do not match expected scopes: {grant_shapes}")

    labels_by_request = {request_id: label for label, request_id in api_request_ids.items()}
    audit_by_label: dict[str, list[dict[str, object]]] = {
        label: [] for label in inspected_labels
    }
    for row in audit_rows:
        label = labels_by_request.get(str(row["request_id"]))
        if label not in audit_by_label:
            raise RuntimeError("Unexpected audit request ID in scoped query")
        audit_by_label[label].append(
            {
                "id": int(row["id"]),
                "action": row["action"],
                "resource_type": row["resource_type"],
                "actor_user_id": row["actor_user_id"],
                "resource_id": row["resource_id"],
                "tenant_id": row["tenant_id"],
                "property_id": row["property_id"],
                "request_id_hash": _hash(str(row["request_id"])),
                "details": _json_value(row["details_json"]),
                "created_at": _json_value(row["created_at"]),
            }
        )

    expected_sla_audits = {
        "tenant-default-create": 1,
        "property-override-create": 1,
        "property-override-same-value-replay": 0,
        "property-override-first-update": 1,
        "property-override-stale-update": 0,
        "policy-change-after-work-order": 1,
    }
    for label, expected_count in expected_sla_audits.items():
        records = audit_by_label[label]
        if len(records) != expected_count:
            raise RuntimeError(f"Unexpected audit count for {label}: {len(records)}")
        if any(
            record["action"] != "SLA_POLICY_CHANGED"
            or record["resource_type"] != "SLA_POLICY"
            for record in records
        ):
            raise RuntimeError(f"Unexpected audit type for {label}")
    concurrent_counts = {
        label: len(audit_by_label[label]) for label in ("concurrent-1", "concurrent-2")
    }
    if sorted(concurrent_counts.values()) != [0, 1]:
        raise RuntimeError(f"Concurrent pair did not create exactly one audit: {concurrent_counts}")
    fixture_audits = audit_by_label["fixture-property-admin-grant"]
    if (
        len(fixture_audits) != 1
        or fixture_audits[0]["action"] != "IAM_USER_ROLE_GRANTED"
    ):
        raise RuntimeError("Fresh Property Admin fixture grant was not audited once")

    policy_by_id = {int(row["id"]): row for row in policy_rows}
    final_policy = policy_by_id[int(state["property_policy_final"]["id"])]
    if (
        int(final_policy["respond_minutes"])
        != int(state["property_policy_final"]["respond_minutes"])
        or int(final_policy["complete_minutes"])
        != int(state["property_policy_final"]["complete_minutes"])
        or final_policy["status"] != "ACTIVE"
    ):
        raise RuntimeError("Final property policy row does not match the API result")

    applied = state["property_policy_before_work_order"]
    created_at = order_row["created_at"]
    due_at = order_row["sla_due_at"]
    complete_due_at = order_row["complete_due_at"]
    warning_at = order_row["warning_at"]
    completion_minutes = round((due_at - created_at).total_seconds() / 60)
    warning_lead_minutes = round((due_at - warning_at).total_seconds() / 60)
    if int(order_row["sla_policy_id"]) != int(applied["id"]):
        raise RuntimeError("Work order SLA policy foreign key does not match the applied policy")
    if completion_minutes != int(applied["complete_minutes"]):
        raise RuntimeError("Work order completion deadline does not match applied SLA minutes")
    if complete_due_at != due_at:
        raise RuntimeError("Work order and SLA clock completion deadlines differ")
    expected_warning_lead = min(
        int(applied["complete_minutes"]),
        max(int(applied["complete_minutes"]) // 4, 15),
    )
    if warning_lead_minutes != expected_warning_lead:
        raise RuntimeError("SLA warning lead does not match current rule")
    assigned_at = order_row["assignment_created_at"]
    if assigned_at is not None:
        accept_minutes = round(
            (order_row["accept_due_at"] - assigned_at).total_seconds() / 60
        )
        respond_minutes = round(
            (order_row["respond_due_at"] - assigned_at).total_seconds() / 60
        )
        if accept_minutes != int(applied["respond_minutes"]) or respond_minutes != int(
            applied["respond_minutes"]
        ):
            raise RuntimeError("Assignment SLA windows do not match the applied policy")
    else:
        accept_minutes = round(
            (order_row["accept_due_at"] - created_at).total_seconds() / 60
        )
        respond_minutes = round(
            (order_row["respond_due_at"] - created_at).total_seconds() / 60
        )

    before_snapshot = state["work_order_before_policy_change"]
    if (
        str(before_snapshot["sla_due_at"]) != order_row["sla_due_at"].isoformat()
        or str(before_snapshot["complete_due_at"])
        != order_row["complete_due_at"].isoformat()
    ):
        raise RuntimeError("Existing work-order completion deadline changed after API evidence")

    evidence = {
        "task_id": TASK_ID,
        "phase": "database_and_audit_read_only",
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": "7814d45531a91efb6a63c63df261415fa446a1c4",
        "run_id": state["run_id"],
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "database_name": database_name,
            "revision": revision,
            "transaction_mode": "READ ONLY",
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
        },
        "rbac": {
            "permission_id": int(role_rows[0]["permission_id"]),
            "role_codes": role_codes,
            "normal_session_grants": [
                {
                    "user_id": int(row["user_id"]),
                    "role_code": row["role_code"],
                    "scope_type": row["scope_type"],
                    "tenant_id": row["tenant_id"],
                    "property_id": row["property_id"],
                }
                for row in user_grants
            ],
        },
        "policies": [
            {
                key: _json_value(value)
                for key, value in row.items()
            }
            for row in policy_rows
        ],
        "audit": audit_by_label,
        "work_order": {
            "id": int(order_row["id"]),
            "tenant_id": int(order_row["tenant_id"]),
            "property_id": int(order_row["property_id"]),
            "house_id": int(order_row["house_id"]),
            "status": order_row["status"],
            "category": order_row["category"],
            "priority": order_row["priority"],
            "sla_policy_id": int(order_row["sla_policy_id"]),
            "created_at": _json_value(created_at),
            "assigned_at": _json_value(order_row["assigned_at"]),
            "sla_due_at": _json_value(due_at),
            "accept_due_at": _json_value(order_row["accept_due_at"]),
            "respond_due_at": _json_value(order_row["respond_due_at"]),
            "complete_due_at": _json_value(complete_due_at),
            "warning_at": _json_value(warning_at),
            "clock_state": order_row["clock_state"],
            "current_assignment_id": order_row["current_assignment_id"],
            "automatic_assignment": order_row["assigned_by_user_id"] is None
            and str(order_row["assignment_reason"] or "").startswith("AUTO_"),
            "applied_respond_minutes": int(applied["respond_minutes"]),
            "applied_complete_minutes": completion_minutes,
            "warning_lead_minutes": warning_lead_minutes,
            "existing_completion_deadline_unchanged": True,
            "policy_row_now_differs_from_applied_snapshot": (
                int(final_policy["respond_minutes"]) != int(applied["respond_minutes"])
                or int(final_policy["complete_minutes"])
                != int(applied["complete_minutes"])
            ),
        },
        "checks": {
            "permission_unique_and_role_mapping_exact": True,
            "normal_accounts_have_expected_scoped_grants": True,
            "same_value_and_stale_writes_have_no_audit": True,
            "concurrent_pair_has_one_audit": True,
            "all_successful_policy_changes_audited": True,
            "work_order_uses_selected_policy": True,
            "completion_and_warning_deadlines_match_applied_policy": True,
            "later_policy_change_did_not_rewrite_existing_completion_deadline": True,
        },
        "boundaries": {
            "database_session_read_only": True,
            "direct_database_business_write": False,
            "migration_run": False,
            "real_model_claimed": False,
            "production_provisioning_claimed": False,
        },
        "redaction": "No phone, token, OTP, password, resident text, or full request ID is emitted.",
    }
    PUBLIC_EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
