"""Correlate SLA Mini Program writes with persisted rows and audit records."""

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
OUT = Path(__file__).resolve().parent
PAGE_EVIDENCE = OUT / "page-evidence.json"
PRIVATE_STATE = REPO / "tmp" / "sla-policy-management-010" / "context.json"
PUBLIC_EVIDENCE = OUT / "page-database-evidence.json"


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
    page = json.loads(PAGE_EVIDENCE.read_text(encoding="utf-8"))
    private = json.loads(PRIVATE_STATE.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=True)
    _guard(settings)
    engine = create_database_engine(settings.active_database_url)
    tenant_policy_id = int(page["objects"]["tenant_policy_id"])
    property_policy_id = int(page["objects"]["property_policy_id"])
    expected_hashes = {
        "tenant_save": page["requests"]["tenant_save"]["put"]["request_id_hash"],
        "property_save": page["requests"]["property_save"]["put"]["request_id_hash"],
        "property_disable": page["requests"]["property_disable"]["put"]["request_id_hash"],
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            database_name = connection.execute(text("SELECT DATABASE()")) .scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            policies = connection.execute(
                text(
                    "SELECT id, tenant_id, property_id, category, priority, "
                    "respond_minutes, complete_minutes, status, updated_at "
                    "FROM sla_policies WHERE id IN :policy_ids ORDER BY id"
                ).bindparams(bindparam("policy_ids", expanding=True)),
                {"policy_ids": [tenant_policy_id, property_policy_id]},
            ).mappings().all()
            audits = connection.execute(
                text(
                    "SELECT id, action, resource_type, actor_user_id, resource_id, "
                    "tenant_id, property_id, request_id, details_json, created_at "
                    "FROM audit_logs WHERE action = 'SLA_POLICY_CHANGED' "
                    "AND resource_id IN :policy_ids ORDER BY id"
                ).bindparams(bindparam("policy_ids", expanding=True)),
                {"policy_ids": [tenant_policy_id, property_policy_id]},
            ).mappings().all()
            connection.rollback()
    finally:
        engine.dispose()

    if database_name != "property_ai_auth001_test" or revision != "20260919_0009":
        raise RuntimeError("Page database evidence target or revision changed")
    if len(policies) != 2:
        raise RuntimeError("Page-created SLA policies are missing")
    by_id = {int(row["id"]): row for row in policies}
    tenant = by_id[tenant_policy_id]
    property_policy = by_id[property_policy_id]
    if (
        tenant["property_id"] is not None
        or tenant["category"] != "OTHER"
        or tenant["priority"] != "LOW"
        or int(tenant["respond_minutes"]) != 19
        or int(tenant["complete_minutes"]) != 49
        or tenant["status"] != "ACTIVE"
    ):
        raise RuntimeError("Page-created tenant default did not persist as expected")
    if (
        int(property_policy["property_id"]) != int(page["objects"]["property_id"])
        or property_policy["category"] != "OTHER"
        or property_policy["priority"] != "LOW"
        or int(property_policy["respond_minutes"]) != 12
        or int(property_policy["complete_minutes"]) != 42
        or property_policy["status"] != "DISABLED"
    ):
        raise RuntimeError("Page-created property override did not persist as disabled")

    normalized_audits = [
        {
            "id": int(row["id"]),
            "action": row["action"],
            "resource_type": row["resource_type"],
            "actor_user_id": row["actor_user_id"],
            "resource_id": int(row["resource_id"]),
            "tenant_id": row["tenant_id"],
            "property_id": row["property_id"],
            "request_id_hash": _hash(str(row["request_id"])),
            "details": _json_value(row["details_json"]),
            "created_at": _json_value(row["created_at"]),
        }
        for row in audits
    ]
    audit_by_hash = {row["request_id_hash"]: row for row in normalized_audits}
    if set(audit_by_hash) != set(expected_hashes.values()):
        raise RuntimeError(
            "Page policy audits do not match the three captured page PUT requests"
        )
    if any(
        row["action"] != "SLA_POLICY_CHANGED"
        or row["resource_type"] != "SLA_POLICY"
        for row in normalized_audits
    ):
        raise RuntimeError("Unexpected page policy audit type")
    if int(audit_by_hash[expected_hashes["tenant_save"]]["actor_user_id"]) != int(
        private["sessions"]["manager"]["user_id"]
    ):
        raise RuntimeError("Tenant page audit actor does not match the authenticated manager")
    for label in ("property_save", "property_disable"):
        if int(audit_by_hash[expected_hashes[label]]["actor_user_id"]) != int(
            private["property_admin_user_id"]
        ):
            raise RuntimeError(f"{label} audit actor does not match Property Admin")

    disable_details = audit_by_hash[expected_hashes["property_disable"]]["details"]
    if (
        disable_details["before"]["status"] != "ACTIVE"
        or disable_details["after"]["status"] != "DISABLED"
    ):
        raise RuntimeError("Property disable audit does not preserve before/after state")

    evidence = {
        "task_id": TASK_ID,
        "phase": "page_database_and_audit_read_only",
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": page["code_sha"],
        "run_id": page["run_id"],
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "database_name": database_name,
            "revision": revision,
            "transaction_mode": "READ ONLY",
        },
        "policies": [
            {key: _json_value(value) for key, value in row.items()}
            for row in policies
        ],
        "audit": normalized_audits,
        "checks": {
            "three_page_put_hashes_match_three_audits": True,
            "tenant_page_actor_matches_normal_manager_session": True,
            "property_page_actor_matches_normal_property_admin_session": True,
            "tenant_default_persisted_active": True,
            "property_override_persisted_disabled": True,
            "disable_audit_preserves_active_to_disabled_transition": True,
        },
        "boundaries": {
            "database_session_read_only": True,
            "direct_database_business_write": False,
            "migration_run": False,
            "page_writes_replayed": False,
            "production_claimed": False,
        },
        "redaction": "No phone, token, OTP, password, or full request ID is emitted.",
    }
    PUBLIC_EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
