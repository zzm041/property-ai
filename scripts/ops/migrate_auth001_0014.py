"""Apply the reviewed 0014 migration to the exact persistent AUTH001 target."""
from __future__ import annotations

from datetime import UTC, datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import site
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import begin_owned_transaction
from app.domain.audit import AuditAction, AuditResourceType, AuditSource
from app.domain.iam import OrganizationScope, ScopeType
from app.model.identity import Role, User
from app.service.audit import AuditService
from app.service.rbac_mutation import RbacMutationService

DATABASE = "property_ai_auth001_test"
BEFORE = "20260925_0012_task_intents"
AFTER = "20260926_0014_identity_merge_sla_versions"
OUTPUT = ROOT / "tmp" / "auth001-persist-v2" / "migration-0014.json"
ALLOWED_COUNT_CHANGES = {"permissions", "role_permissions"}
NEW_TABLES = {
    "identity_recovery_cases",
    "identity_recovery_reviews",
    "phone_identity_tombstones",
    "account_merges",
    "sla_policy_versions",
    "work_order_sla_applications",
}
SYNTHETIC_ADMIN_MARKER = "__PERSIST_V2_SYSTEM_ADMIN_20260927__"


def _settings_and_url() -> tuple[Settings, str]:
    env_file = next((p for p in (BACKEND / ".env", ROOT / ".env") if p.exists()), None)
    settings = Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )
    parsed = make_url(settings.test_database_url).set(database=DATABASE)
    if (
        parsed.drivername != "mysql+pymysql"
        or parsed.host != "127.0.0.1"
        or parsed.port != 3307
        or parsed.database != DATABASE
        or not parsed.username
        or not parsed.password
    ):
        raise RuntimeError("0014 runner rejected database target")
    database_url = parsed.render_as_string(hide_password=False)
    isolated = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=database_url,
        database_url=settings.database_url,
        app_secret_key=settings.app_secret_key,
        dev_auth_enabled=False,
    )
    return isolated, database_url


def _checksum(connection, table: str) -> str:
    row = connection.execute(text(f"CHECKSUM TABLE `{table}` EXTENDED")).one()
    return str(row[-1])


def _snapshot(connection) -> dict:
    inspector = inspect(connection)
    tables = sorted(inspector.get_table_names())
    counts = {
        table: connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        for table in tables
    }
    protected = [
        table for table in (
            "users", "user_phones", "user_passwords", "auth_sessions",
            "password_reset_tickets", "work_orders", "sla_clocks", "audit_logs",
        ) if table in tables
    ]
    return {
        "revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
        "tables": tables,
        "counts": counts,
        "protected_checksums": {table: _checksum(connection, table) for table in protected},
        "phone_uniques": sorted(connection.execute(text(
            "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA=:db AND TABLE_NAME='user_phones' "
            "AND CONSTRAINT_TYPE='UNIQUE' AND CONSTRAINT_NAME IN "
            "('uq_user_phones_active_e164','uq_user_phones_active_user')"
        ), {"db": DATABASE}).scalars()),
    }


def _prepare_synthetic_system_admin(engine) -> None:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        with begin_owned_transaction(session):
            role = session.scalar(
                select(Role).where(Role.code.collate("utf8mb4_bin") == "SYSTEM_ADMIN")
            )
            if role is None:
                role = Role(code="SYSTEM_ADMIN", name="System Admin")
                session.add(role)
                session.flush()
                AuditService(session).append(
                    actor_user_id=None,
                    action=AuditAction.DEV_SEED_ROLE_CREATED,
                    resource_type=AuditResourceType.ROLE,
                    resource_id=role.id,
                    request_id="persist-v2-0014-prerequisite",
                    scope_type=ScopeType.SYSTEM,
                    tenant_id=None,
                    property_id=None,
                    details={
                        "role_code": "SYSTEM_ADMIN",
                        "source": AuditSource.DEVELOPMENT_SEED.value,
                    },
                )
            existing = session.scalars(
                select(User).where(
                    User.display_name.collate("utf8mb4_bin") == SYNTHETIC_ADMIN_MARKER
                ).limit(2)
            ).all()
            if existing:
                raise RuntimeError("narrow synthetic prerequisite marker already exists")
            user = User(status="ACTIVE", display_name=SYNTHETIC_ADMIN_MARKER)
            session.add(user)
            session.flush()
            AuditService(session).append(
                actor_user_id=None,
                action=AuditAction.DEV_SEED_USER_CREATED,
                resource_type=AuditResourceType.USER,
                resource_id=user.id,
                request_id="persist-v2-0014-prerequisite",
                scope_type=ScopeType.SYSTEM,
                tenant_id=None,
                property_id=None,
                details={
                    "seed_key": SYNTHETIC_ADMIN_MARKER,
                    "source": AuditSource.DEVELOPMENT_SEED.value,
                },
            )
            RbacMutationService(session).grant_user_role_for_development_seed(
                target_user_id=user.id,
                role_code="SYSTEM_ADMIN",
                scope=OrganizationScope(ScopeType.SYSTEM),
                request_id="persist-v2-0014-prerequisite",
            )


def _inspect_partial_state(engine) -> dict:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        version_column = next(
            column for column in inspector.get_columns("alembic_version")
            if column["name"] == "version_num"
        )
        new_counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
            for table in sorted(NEW_TABLES & tables)
        }
        permission_count = connection.execute(text(
            "SELECT COUNT(*) FROM permissions WHERE code='identity:review'"
        )).scalar_one()
        grant_count = connection.execute(text(
            "SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON r.id=rp.role_id "
            "JOIN permissions p ON p.id=rp.permission_id "
            "WHERE r.code='SYSTEM_ADMIN' AND p.code='identity:review'"
        )).scalar_one()
        return {
            "observed_utc": datetime.now(UTC).isoformat(),
            "revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
            "alembic_version_type": str(version_column["type"]),
            "present_new_tables": sorted(NEW_TABLES & tables),
            "missing_new_tables": sorted(NEW_TABLES - tables),
            "new_table_counts": new_counts,
            "identity_permission_count": permission_count,
            "system_admin_grant_count": grant_count,
            "status": "PARTIAL_DDL_REVISION_NOT_ADVANCED",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-synthetic-prerequisite", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()
    settings, database_url = _settings_and_url()
    engine = create_engine(database_url, pool_pre_ping=True)
    if args.inspect_only:
        evidence = _inspect_partial_state(engine)
        engine.dispose()
        path = ROOT / "tmp" / "auth001-persist-v2" / "migration-0014-failure.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False))
        return 0
    with engine.connect() as connection:
        system_admins = connection.execute(text(
            "SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
            "WHERE r.code='SYSTEM_ADMIN'"
        )).scalar_one()
        prerequisite_marker = connection.execute(text(
            "SELECT COUNT(*) FROM users WHERE display_name=:marker"
        ), {"marker": SYNTHETIC_ADMIN_MARKER}).scalar_one()
    seeded = False
    if not system_admins:
        if not args.prepare_synthetic_prerequisite:
            raise RuntimeError("0014 synthetic SYSTEM_ADMIN prerequisite is absent")
        _prepare_synthetic_system_admin(engine)
        seeded = True
    with engine.connect() as connection:
        before = _snapshot(connection)
    if before["revision"] != BEFORE:
        raise RuntimeError("0014 runner requires exact 0012")
    if before["phone_uniques"] != ["uq_user_phones_active_e164", "uq_user_phones_active_user"]:
        raise RuntimeError("phone uniqueness preconditions are missing")

    os.environ["APP_ENV"] = "test"
    os.environ["PROPERTY_AI_APPROVE_0014_DATABASE"] = DATABASE
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, AFTER)

    with engine.connect() as connection:
        after = _snapshot(connection)
        identity_counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
            for table in NEW_TABLES - {"sla_policy_versions"}
        }
        sla_versions = connection.execute(text("SELECT COUNT(*) FROM sla_policy_versions")).scalar_one()
        sla_policies = connection.execute(text("SELECT COUNT(*) FROM sla_policies")).scalar_one()
        identity_permission = connection.execute(text(
            "SELECT COUNT(*) FROM permissions WHERE code='identity:review'"
        )).scalar_one()
        system_admin_grant = connection.execute(text(
            "SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON r.id=rp.role_id "
            "JOIN permissions p ON p.id=rp.permission_id "
            "WHERE r.code='SYSTEM_ADMIN' AND p.code='identity:review'"
        )).scalar_one()
    engine.dispose()

    if after["revision"] != AFTER:
        raise RuntimeError("0014 revision was not recorded")
    if set(after["tables"]) - set(before["tables"]) != NEW_TABLES:
        raise RuntimeError("0014 table delta differs from the reviewed six tables")
    if after["phone_uniques"] != before["phone_uniques"]:
        raise RuntimeError("phone uniqueness guards changed")
    for table, count in before["counts"].items():
        if table not in ALLOWED_COUNT_CHANGES and after["counts"][table] != count:
            raise RuntimeError(f"unexpected existing-row count change in {table}")
    if after["protected_checksums"] != before["protected_checksums"]:
        raise RuntimeError("protected historical rows changed during 0014")
    if any(identity_counts.values()):
        raise RuntimeError("0014 fabricated identity or historical work-order rows")
    if sla_versions != sla_policies or identity_permission != 1 or system_admin_grant < 1:
        raise RuntimeError("0014 baseline or RBAC delta is incomplete")

    evidence = {
        "task_id": "PENDING0014-RUNTIME",
        "observed_utc": datetime.now(UTC).isoformat(),
        "target": "127.0.0.1:3307/property_ai_auth001_test",
        "synthetic_prerequisite_seeded": seeded,
        "synthetic_prerequisite_marker_present": prerequisite_marker == 1 or seeded,
        "before_revision": BEFORE,
        "after_revision": AFTER,
        "new_tables": sorted(NEW_TABLES),
        "protected_history_unchanged": True,
        "phone_uniques": after["phone_uniques"],
        "identity_rows_created_by_migration": identity_counts,
        "sla_policy_versions": sla_versions,
        "sla_policies": sla_policies,
        "identity_permission_count": identity_permission,
        "system_admin_grant_count": system_admin_grant,
        "existing_counts_before_sha256": hashlib.sha256(
            json.dumps(before["counts"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
