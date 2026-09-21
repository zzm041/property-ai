"""Provision and verify SLA RBAC through the guarded AUTH001 Development Seed.

This script is intentionally limited to the already-approved isolated AUTH001
database.  It never writes RBAC rows directly: all mutations are performed by
the existing idempotent Development Seed service, then independently queried.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import make_url


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import create_database_engine  # noqa: E402
from app.service.dev_seed import run_development_seed  # noqa: E402
from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "SLA-POLICY-MGMT-010"
OUT = Path(__file__).resolve().parent / "rbac-provision-evidence.json"
EXPECTED_ROLES = {"PROPERTY_ADMIN", "TENANT_ADMIN"}


def _guard(settings) -> dict[str, object]:
    database = make_url(settings.active_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = {
        "host": database.host,
        "port": database.port,
        "database": database.database,
        "app_env": settings.app_env,
        "allow_destructive_test_database": settings.allow_destructive_test_database,
        "queue": settings.celery_task_queue,
        "vhost": broker.path.lstrip("/"),
    }
    expected = {
        "host": "127.0.0.1",
        "port": 3307,
        "database": "property_ai_auth001_test",
        "app_env": "test",
        "allow_destructive_test_database": True,
        "queue": ISOLATED_QUEUE,
        "vhost": ISOLATED_VHOST,
    }
    if actual != expected:
        raise RuntimeError(f"AUTH001 target guard rejected settings: {actual}")
    return actual


def _snapshot(engine) -> dict[str, object]:
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        permission_count = connection.execute(
            text("SELECT COUNT(*) FROM permissions WHERE code = 'sla:manage'")
        ).scalar_one()
        role_codes = connection.execute(
            text(
                "SELECT r.code "
                "FROM roles AS r "
                "JOIN role_permissions AS rp ON rp.role_id = r.id "
                "JOIN permissions AS p ON p.id = rp.permission_id "
                "WHERE p.code = 'sla:manage' "
                "ORDER BY r.code"
            )
        ).scalars().all()
        duplicate_pairs = connection.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT rp.role_id, rp.permission_id, COUNT(*) AS row_count "
                "FROM role_permissions AS rp "
                "JOIN permissions AS p ON p.id = rp.permission_id "
                "WHERE p.code = 'sla:manage' "
                "GROUP BY rp.role_id, rp.permission_id HAVING COUNT(*) > 1"
                ") AS duplicated"
            )
        ).scalar_one()
    return {
        "revision": revision,
        "permission_row_count": int(permission_count),
        "role_codes": list(role_codes),
        "duplicate_role_permission_pairs": int(duplicate_pairs),
    }


def main() -> int:
    if OUT.exists():
        raise RuntimeError(
            "RBAC evidence already exists; inspect it instead of silently rerunning Seed"
        )
    settings = build_isolated_settings(enable_enqueue=True)
    target = _guard(settings)
    engine = create_database_engine(settings.active_database_url)
    run_id = f"sla-policy-rbac-{secrets.token_hex(5)}"
    try:
        before = _snapshot(engine)
        if before["revision"] != "20260919_0009":
            raise RuntimeError(f"Unexpected AUTH001 revision: {before['revision']}")

        result = run_development_seed(
            settings,
            request_id=f"{run_id}-development-seed",
        )
        after = _snapshot(engine)
        if after["revision"] != "20260919_0009":
            raise RuntimeError("Development Seed changed the Alembic revision")
        if after["permission_row_count"] != 1:
            raise RuntimeError("sla:manage permission is missing or duplicated")
        if set(after["role_codes"]) != EXPECTED_ROLES:
            raise RuntimeError(
                f"Unexpected sla:manage role mapping: {after['role_codes']}"
            )
        if after["duplicate_role_permission_pairs"] != 0:
            raise RuntimeError("Duplicate sla:manage role-permission mapping detected")

        evidence = {
            "task_id": TASK_ID,
            "phase": "guarded_rbac_provision",
            "captured_at": datetime.now(UTC).isoformat(),
            "code_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "run_id": run_id,
            "environment": {
                **target,
                "revision": after["revision"],
            },
            "method": {
                "mutation": "existing guarded idempotent Development Seed service",
                "request_id": result.request_id,
                "direct_sql_business_write": False,
                "migration_run": False,
            },
            "before": before,
            "after": after,
            "checks": {
                "revision_remained_0009": True,
                "sla_permission_unique": True,
                "administrator_role_mapping_exact": True,
                "other_frozen_roles_not_mapped": True,
            },
            "boundaries": {
                "database": "property_ai_auth001_test only",
                "property_ai_test_touched": False,
                "shared_vhost_read_consumed_replayed_or_cleared": False,
                "seed_login_used": False,
                "production_provisioning_claimed": False,
            },
        }
        OUT.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
