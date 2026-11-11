#!/usr/bin/env python3
"""Create or check the disposable test schema (PA-118).

`deploy/docker-compose.yml` 的 `mysql-test` 服务（profile `test`，tmpfs）只创建空库
`property_ai_test`，不跑迁移。于是 integration 全量会一条一条挂：

    E  pymysql.err.ProgrammingError: (1146, "Table 'property_ai_test.users' doesn't exist")

这个脚本把「建库」补齐成「建库 + 迁到 head」，并给 `--check` 一个可脚本化的判据：

    APP_ENV=test \
    TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \
    python scripts/ops/prepare_test_database.py            # 迁到 head（幂等）

    ... python scripts/ops/prepare_test_database.py --check  # 只检查，缺 schema 就 exit 1

安全边界（与 `assert_disposable_test_database` 同源，但不要求
`ALLOW_DESTRUCTIVE_TEST_DATABASE`）：只允许 `APP_ENV=test`、只允许库名含 `test`、
必须与 `DATABASE_URL` 不同、拒绝 delivery/prod 之类受保护的库名。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.database import (  # noqa: E402
    TEST_SCHEMA_MARKER_TABLES,
    create_database_engine,
)

#: Migration the empty-install authority stops at before the baseline administrator
#: is created (same split as `app.cli.customer_install`).
PRE_BOOTSTRAP = "20260925_0012_task_intents"
#: Migration whose guard demands that administrator already exist.
HEAD = "20261111_0018_content_fingerprint_loose"
#: Fixed identity for the throwaway schema's baseline administrator. Integration tests
#: wipe `users` between runs, so this is provenance, not an account anybody uses.
DEFAULT_BOOTSTRAP_PHONE = "13800000000"
BASELINE_ADMIN_NAME = "Test Schema Bootstrap Administrator"
DEFAULT_BOOTSTRAP_PASSWORD = "Test-Bootstrap-Password-1"

#: Never run a migration against these, whatever the name of the env var says.
PROTECTED_DATABASES = frozenset(
    {
        "property_ai",
        "property_ai_delivery_check",
        "property_ai_day4",
        "property_ai_restore_drill",
        "mysql",
        "sys",
        "information_schema",
        "performance_schema",
    }
)


class PreparationRefused(RuntimeError):
    """Target is not an explicitly disposable test database."""


def resolve_target(settings: Settings) -> str:
    """Return TEST_DATABASE_URL after proving it is a throwaway schema."""

    target = settings.test_database_url
    parsed = make_url(target)
    if settings.app_env != "test":
        raise PreparationRefused(
            f"APP_ENV must be test (got {settings.app_env!r}); this script migrates a"
            " disposable schema, never the development one"
        )
    if parsed.drivername != "mysql+pymysql":
        raise PreparationRefused(
            f"TEST_DATABASE_URL must use mysql+pymysql (got {parsed.drivername!r})"
        )
    database = (parsed.database or "").casefold()
    if "test" not in database:
        raise PreparationRefused(
            "the test database name must contain 'test'"
            f" (got {parsed.database!r}); refusing to migrate it"
        )
    if database in PROTECTED_DATABASES:
        raise PreparationRefused(f"{parsed.database!r} is a protected database")
    development = make_url(settings.database_url)
    if (parsed.host, parsed.port, parsed.database) == (
        development.host,
        development.port,
        development.database,
    ):
        raise PreparationRefused(
            "TEST_DATABASE_URL points at the development database"
            f" ({parsed.host}:{parsed.port}/{parsed.database})"
        )
    return target


def head_revision() -> str:
    """Return the migration head this checkout expects a test schema to be at."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise PreparationRefused(f"expected exactly one alembic head, found {heads}")
    return heads[0]


def schema_state(target: str) -> tuple[list[str], str | None, int]:
    """Return (missing marker tables, alembic revision, table count) for a target."""

    engine = create_database_engine(target, connect_timeout=10)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = inspector.get_table_names()
            missing = [name for name in TEST_SCHEMA_MARKER_TABLES if name not in tables]
            revision = None
            if "alembic_version" in tables:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            return missing, revision, len(tables)
    finally:
        engine.dispose()


def system_admin_count(connection) -> int:
    """Count actors holding SYSTEM_ADMIN; migration 0014 requires at least one."""

    return connection.execute(
        text(
            "SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
            " WHERE r.code = 'SYSTEM_ADMIN'"
        )
    ).scalar_one()


def bootstrap_phone() -> str:
    return os.environ.get("TEST_BOOTSTRAP_ADMIN_PHONE", DEFAULT_BOOTSTRAP_PHONE)


def bootstrap_password() -> str:
    return os.environ.get("TEST_BOOTSTRAP_ADMIN_PASSWORD", DEFAULT_BOOTSTRAP_PASSWORD)


def clear_baseline_admin(connection) -> int:
    """Delete the baseline administrator that only existed for 0014's guard.

    Migration 0014 records provenance against an existing SYSTEM_ADMIN actor, so the
    schema cannot be built without one. Once the schema is at head that actor has done
    its job, and leaving it behind would make `users` non-empty behind a `user_passwords`
    ON DELETE RESTRICT row — exactly what the integration cleanup fixtures (which delete
    `users` but not its child rows) cannot cope with.
    """

    phone = bootstrap_phone()
    user_id = connection.execute(
        text("SELECT id FROM users WHERE display_name = :name ORDER BY id LIMIT 1"),
        {"name": BASELINE_ADMIN_NAME},
    ).scalar_one_or_none()
    if user_id is None:
        return 0
    removed = 0
    # Child rows first, and none of these have ON DELETE CASCADE: bootstrap writes a
    # password, a phone, a role grant and an audit entry, and each one RESTRICTs the
    # delete of its user.
    for statement in (
        "DELETE FROM user_passwords WHERE user_id = :id",
        "DELETE FROM user_phones WHERE user_id = :id",
        "DELETE FROM user_roles WHERE user_id = :id",
        "DELETE FROM audit_logs WHERE actor_user_id = :id",
        "DELETE FROM users WHERE id = :id",
    ):
        removed += connection.execute(text(statement), {"id": user_id}).rowcount or 0
    return removed


def upgrade_to_head(target: str, *, keep_baseline_admin: bool = False) -> None:
    """Bring an empty or partial test schema to head.

    A bare `alembic upgrade head` cannot build this schema, for two independent
    reasons, and the fix is the same three stages `app.cli.customer_install` uses:

    * migrations 0009 and 0011 refuse any target other than the isolated AUTH001
      database, the delivery `mysql:3306/property_ai_delivery_check` target, or a
      connection carrying the repo's in-process `CustomerInstallAuthority`. A
      disposable test schema is exactly the "approved empty install" that authority
      exists for, so attach it here and deactivate it before returning.
    * migration 0014 requires a SYSTEM_ADMIN actor that already exists, because it
      records baseline provenance against it. A schema that was just created has no
      users at all, so `upgrade 0012 -> 0014` fails on its own guard. Bootstrap one
      administrator at exactly 0012, the way the installer does.

    The guard against migrating something real lives in `resolve_target()`:
    APP_ENV=test, database name contains "test", differs from DATABASE_URL, and not a
    protected name. The authority only ever covers this one connection.

    The guard against migrating something real lives in `resolve_target()`:
    APP_ENV=test, database name contains "test", differs from DATABASE_URL, and not a
    protected name. The authority only ever covers this one connection.
    """

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.cli.bootstrap_system_admin import _bootstrap
    from app.cli.customer_install_authority import CustomerInstallAuthority
    from app.core.database import create_database_engine

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    parsed = make_url(target)
    label = f"{parsed.host}:{parsed.port or 3306}/{parsed.database}"
    engine = create_database_engine(target, connect_timeout=10)
    try:
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            if "alembic_version" not in tables:
                # Alembic bookkeeping only: historical revision ids exceed the
                # default VARCHAR(32), same workaround customer_install uses.
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version"
                        " (version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
                    )
                )
                connection.commit()
            else:
                # A schema that was migrated once already owns alembic's default
                # VARCHAR(32) column; 0014's revision id does not fit in it.
                connection.execute(
                    text(
                        "ALTER TABLE alembic_version"
                        " MODIFY version_num VARCHAR(64) NOT NULL"
                    )
                )
                connection.commit()
            revisions = frozenset(
                item.revision for item in ScriptDirectory.from_config(config).walk_revisions()
            )
            authority = CustomerInstallAuthority(connection, label, revisions)
            try:
                config.attributes.update(
                    connection=connection, customer_install_authority=authority
                )
                command.upgrade(config, PRE_BOOTSTRAP)
                connection.commit()
                if not system_admin_count(connection):
                    # `_bootstrap` reads the display name from the environment, so the
                    # cleanup step below can find the row it created.
                    os.environ.setdefault("BOOTSTRAP_ADMIN_DISPLAY_NAME", BASELINE_ADMIN_NAME)
                    _bootstrap(
                        target,
                        bootstrap_phone(),
                        bootstrap_password(),
                        connection=connection,
                    )
                    connection.commit()
                command.upgrade(config, HEAD)
                connection.commit()
                if not keep_baseline_admin:
                    removed = clear_baseline_admin(connection)
                    connection.commit()
                    if removed:
                        print(
                            f"removed the baseline administrator ({removed} rows) so the"
                            " schema is data-empty again"
                        )
            finally:
                authority.active = False
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether the test schema is ready (exit 1 when it is not)",
    )
    parser.add_argument(
        "--keep-baseline-admin",
        action="store_true",
        help="keep the baseline administrator created for migration 0014",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print one line per run instead of the before/after report",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        target = resolve_target(settings)
    except PreparationRefused as exc:
        print(f"TEST_DATABASE_REFUSED: {exc}")
        return 2
    parsed = make_url(target)
    label = f"{parsed.host}:{parsed.port or 3306}/{parsed.database}"

    try:
        missing, revision, tables = schema_state(target)
    except Exception as exc:  # noqa: BLE001 - operator-facing message, not a crash dump
        print(
            f"TEST_DATABASE_UNREACHABLE: cannot connect to {label} ({exc.__class__.__name__})."
            " Start it with: docker compose -f deploy/docker-compose.yml --profile test up -d mysql-test"
        )
        return 2

    head = head_revision()
    if not missing and revision == head:
        if not args.quiet:
            print(f"test schema ready: {label} (revision {revision}, {tables} tables)")
        return 0

    if args.check:
        if missing:
            detail = f"has no application schema (missing table(s): {', '.join(missing)}"
        else:
            detail = f"is behind head (at {revision}, head is {head}"
        print(
            f"TEST_DATABASE_SCHEMA_MISSING: {label} {detail};"
            f" {tables} tables present). Run this script without --check to migrate to head."
        )
        return 1

    print(
        f"migrating {label} (before: {tables} tables, revision {revision}, head {head})"
    )
    upgrade_to_head(target, keep_baseline_admin=args.keep_baseline_admin)
    missing_after, revision_after, tables_after = schema_state(target)
    if missing_after or revision_after != head:
        print(
            f"TEST_DATABASE_SCHEMA_MISSING: {label} is still not ready after the upgrade"
            f" (revision {revision_after}, head {head}, missing {', '.join(missing_after) or '-'})"
        )
        return 1
    print(
        f"test schema ready: {label} (revision {revision_after}, {tables_after} tables)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
