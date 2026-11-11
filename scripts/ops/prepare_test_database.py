#!/usr/bin/env python3
"""Create or check the disposable test schema (PA-118 / PA-174 / PA-177).

`deploy/docker-compose.yml` 的 `mysql-test` 服务（profile `test`，tmpfs）只创建空库
`property_ai_test`，不跑迁移。于是 integration 全量会一条一条挂：

    E  pymysql.err.ProgrammingError: (1146, "Table 'property_ai_test.users' doesn't exist")

这个脚本把「建库」补齐成「建库 + 迁到 head」，并给 `--check` 一个可脚本化的判据：

    APP_ENV=test \
    TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \
    python scripts/ops/prepare_test_database.py --yes-i-mean-it   # 迁到 head（幂等）

    ... python scripts/ops/prepare_test_database.py --check        # 只检查，缺 schema 就 exit 1

安全边界（PA-174）。只允许「本机 host + 名字确实是一次性测试库」的目标：

1. host 必须在白名单内。默认只认回环（`127.0.0.1` / `localhost` / `::1` / 空）；
   需要放行别的地址时显式给 `PREPARE_TEST_DB_ALLOWED_HOSTS=a,b`。远端一律拒。
2. 库名必须像测试库（含 `test`），并且**不**落在保护名单里：既查精确名
   （`PROTECTED_DATABASES`），也查危险分段（`prod` / `production` / `staging` / `live`
   / `delivery` / `restore` / `drill` / `backup`）。所以 `property_ai_test_staging`
   这类「含 test 但其实是业务库」的名字会被拒——PA-174 之前正是这里漏了。
   需要额外保护自有业务前缀时给 `PREPARE_TEST_DB_PROTECTED_PREFIXES=acme_,core_`。
3. 迁移前要二次确认：非 `--check` 时必须 `--yes-i-mean-it`，或者在终端里手打库名。

半迁移库（PA-177）。「有表、但 `alembic_version` 缺表或为空」的库（本机 3307 的
`property_ai_test` 就是）直接 upgrade 会撞 `(1050, "Table 'tenants' already exists")`。
脚本现在会先判断结构：与 head 模型一致就 stamp 到 head，否则按迁移脚本推导出的
表集合推断最近 revision 再 stamp，然后照常 upgrade；推断不出来时给出两条可执行选项
（drop 重建 / `--stamp <rev>`），不再抛裸堆栈。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
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
IDENTITY = "20260926_0014_identity_merge_sla_versions"
#: Migration head this checkout expects a prepared test schema to sit at.
HEAD = "20261112_0021_ai_call_log_quarantine_scope"
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

#: A name made of these segments is a business database that merely mentions "test".
#: PA-174: `property_ai_test_staging` / `property_ai_prod_test` used to pass because the
#: only name rule was "contains 'test'" and `PROTECTED_DATABASES` holds no such name.
PROTECTED_SEGMENTS = frozenset(
    {"prod", "production", "staging", "live", "delivery", "restore", "drill", "backup"}
)

#: Hosts a disposable test schema may live on. Anything else needs an explicit opt-in.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})
_ALLOWED_HOSTS_ENV = "PREPARE_TEST_DB_ALLOWED_HOSTS"
_PROTECTED_PREFIXES_ENV = "PREPARE_TEST_DB_PROTECTED_PREFIXES"

#: DDL statements as written by the repo's migrations (`op.create_table('name', ...)`).
_CREATE_TABLE = re.compile(r"op\.create_table\(\s*(['\"])([A-Za-z_][\w]*)\1")
_DROP_TABLE = re.compile(r"op\.drop_table\(\s*(['\"])([A-Za-z_][\w]*)\1")
_RENAME_TABLE = re.compile(
    r"op\.rename_table\(\s*(['\"])([A-Za-z_][\w]*)\1\s*,\s*(['\"])([A-Za-z_][\w]*)\3"
)


class PreparationRefused(RuntimeError):
    """Target is not an explicitly disposable test database."""


class HalfMigratedSchema(RuntimeError):
    """Schema has application tables but no usable `alembic_version` row (PA-177)."""


@dataclass(frozen=True)
class SchemaState:
    """What the target database looks like right now."""

    missing_markers: tuple[str, ...]
    revision: str | None
    tables: tuple[str, ...]

    @property
    def application_tables(self) -> tuple[str, ...]:
        """Tables the application owns; alembic's bookkeeping is not one of them."""

        return tuple(name for name in self.tables if name != "alembic_version")

    @property
    def is_empty(self) -> bool:
        return not self.application_tables


def _split_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def allowed_hosts() -> frozenset[str]:
    """Local addresses plus whatever `PREPARE_TEST_DB_ALLOWED_HOSTS` adds."""

    return LOCAL_HOSTS | frozenset(host.casefold() for host in _split_env(_ALLOWED_HOSTS_ENV))


def protected_prefixes() -> tuple[str, ...]:
    """Extra business prefixes that must never be migrated as a test schema."""

    return tuple(prefix.casefold() for prefix in _split_env(_PROTECTED_PREFIXES_ENV))


def check_database_name(database: str) -> None:
    """Raise when `database` is not obviously a throwaway test schema (PA-174)."""

    name = (database or "").casefold()
    # Protect the known names first: `property_ai` and `mysql` contain no "test", so a
    # "must look like a test database" rule ahead of this one would make the whole
    # protected list unreachable — exactly the PA-174 shape.
    if name in PROTECTED_DATABASES:
        raise PreparationRefused(f"{database!r} is a protected database")
    segments = [segment for segment in re.split(r"[^a-z0-9]+", name) if segment]
    dangerous = sorted(set(segments) & PROTECTED_SEGMENTS)
    if dangerous:
        raise PreparationRefused(
            f"{database!r} looks like a business database, not a disposable one"
            f" (reserved segment(s): {', '.join(dangerous)})"
        )
    for prefix in protected_prefixes():
        if name.startswith(prefix):
            raise PreparationRefused(
                f"{database!r} starts with the protected prefix {prefix!r}"
                f" (from {_PROTECTED_PREFIXES_ENV})"
            )
    if "test" not in name:
        raise PreparationRefused(
            "the test database name must contain 'test'"
            f" (got {database!r}); refusing to migrate it"
        )


def resolve_target(settings: Settings) -> str:
    """Return TEST_DATABASE_URL after proving it is a throwaway schema on a local host."""

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
    host = (parsed.host or "").casefold()
    if host not in allowed_hosts():
        raise PreparationRefused(
            f"TEST_DATABASE_URL host {parsed.host!r} is not a local test host; refusing to"
            " migrate a remote database. Allowed: "
            f"{', '.join(sorted(allowed_hosts()))}"
            f" (extend with {_ALLOWED_HOSTS_ENV}=host1,host2)"
        )
    check_database_name(parsed.database or "")
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


def confirm_target(label: str, database: str, *, assume_yes: bool) -> None:
    """Require a second confirmation before migrating anything (PA-174, rule 3)."""

    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise PreparationRefused(
            "refusing to migrate without confirmation in a non-interactive shell;"
            f" re-run with --yes-i-mean-it to migrate {label}"
        )
    print(f"About to migrate the disposable test schema {label}.")
    try:
        typed = input(f"Type the database name ({database}) to continue: ").strip()
    except EOFError:
        # A pipe or a detached terminal: the operator never saw the prompt.
        raise PreparationRefused(
            "no answer to the confirmation prompt (end of input); re-run with"
            f" --yes-i-mean-it to migrate {label}"
        ) from None
    if typed.casefold() != (database or "").casefold():
        raise PreparationRefused(
            f"confirmation mismatch (typed {typed!r}, expected {database!r}); nothing was migrated"
        )


def head_revision() -> str:
    """Return the migration head this checkout expects a test schema to be at."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise PreparationRefused(f"expected exactly one alembic head, found {heads}")
    return heads[0]


def revision_order() -> list[str]:
    """Revisions oldest first, as the migration graph orders them."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    ordered = list(scripts.walk_revisions(base="base", head="heads"))
    ordered.reverse()
    return [script.revision for script in ordered]


def revision_table_sets() -> dict[str, frozenset[str]]:
    """Table set each revision leaves behind, derived from the migration scripts.

    PA-177 needs a way to tell "64 tables, no revision recorded" apart from "empty
    schema": the migrations say exactly which tables exist at every revision, so the
    live table list can be matched against them without a database round trip.
    """

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    ordered = list(scripts.walk_revisions(base="base", head="heads"))
    ordered.reverse()
    cumulative: set[str] = set()
    sets: dict[str, frozenset[str]] = {}
    for script in ordered:
        body = Path(script.path).read_text(encoding="utf-8")
        for _, old, _, new in _RENAME_TABLE.findall(body):
            if old in cumulative:
                cumulative.discard(old)
                cumulative.add(new)
        cumulative -= {name for _, name in _DROP_TABLE.findall(body)}
        cumulative |= {name for _, name in _CREATE_TABLE.findall(body)}
        sets[script.revision] = frozenset(cumulative)
    return sets


def infer_revision(application_tables: tuple[str, ...] | list[str]) -> tuple[str | None, str]:
    """Guess the revision a half-migrated schema sits at, from its table list.

    Returns `(revision, why)`. The newest revision whose table set is fully present
    wins; when nothing matches, `revision` is None and `why` says what was missing.
    """

    present = {name for name in application_tables if name != "alembic_version"}
    if not present:
        return None, "the database has no application tables"
    sets = revision_table_sets()
    order = {revision: index for index, revision in enumerate(revision_order())}
    candidates = [
        (len(tables), order.get(revision, -1), revision)
        for revision, tables in sets.items()
        if tables and tables <= present
    ]
    if not candidates:
        return None, "no revision's table set is a subset of the tables found"
    _, _, revision = max(candidates)
    missing = sorted(sets[revision] - present)
    if missing:  # pragma: no cover - guarded by the subset filter above
        return None, f"revision {revision} is missing table(s): {', '.join(missing)}"
    return revision, f"table set matches {len(sets[revision])} tables of {revision}"


def _structure_matches_head(connection) -> bool:
    """True when the live schema already equals the model metadata (i.e. at head)."""

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.model import Base

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return not compare_metadata(context, Base.metadata)


def schema_state(target: str) -> SchemaState:
    """Read the marker tables, recorded revision and table list of a target."""

    engine = create_database_engine(target, connect_timeout=10)
    try:
        with engine.connect() as connection:
            tables = tuple(inspect(connection).get_table_names())
            missing = tuple(name for name in TEST_SCHEMA_MARKER_TABLES if name not in tables)
            revision = None
            if "alembic_version" in tables:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            return SchemaState(missing_markers=missing, revision=revision, tables=tables)
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


def half_migrated_message(
    label: str, state: SchemaState, *, reason: str, known_revisions: list[str]
) -> str:
    """Operator-facing text for a schema with tables but no recorded revision."""

    dumped = ", ".join(known_revisions[-3:]) or "n/a"
    return (
        f"TEST_DATABASE_HALF_MIGRATED: {label} has {len(state.application_tables)}"
        " application table(s) but no recorded alembic revision"
        f" ({reason}). Refusing to guess silently, because a wrong stamp would skip"
        " migrations. Two executable options:\n"
        "  1) drop and rebuild (the disposable test database holds nothing worth keeping):\n"
        "       docker compose -f deploy/docker-compose.yml --profile test down -v mysql-test\n"
        "       docker compose -f deploy/docker-compose.yml --profile test up -d mysql-test\n"
        "       python scripts/ops/prepare_test_database.py --yes-i-mean-it\n"
        f"  2) stamp the revision you know is right, then migrate:\n"
        f"       python scripts/ops/prepare_test_database.py --stamp <revision> --yes-i-mean-it\n"
        f"     known revisions (newest last): {dumped}"
    )


def _stamp_if_half_migrated(
    connection, config, label: str, *, stamp: str | None, state: SchemaState
) -> str:
    """Give a table-laden but unstamped schema a revision, or explain why not (PA-177)."""

    from alembic import command

    order = revision_order()
    sets = revision_table_sets()
    if stamp is not None:
        if stamp not in sets:
            raise HalfMigratedSchema(
                half_migrated_message(
                    label,
                    state,
                    reason=f"--stamp {stamp!r} is not a revision in this checkout",
                    known_revisions=order,
                )
            )
        revision, why = stamp, f"explicit --stamp {stamp}"
    elif _structure_matches_head(connection):
        revision, why = HEAD, "the live schema already equals the model metadata"
    else:
        revision, why = infer_revision(state.application_tables)
        if revision is None:
            raise HalfMigratedSchema(
                half_migrated_message(
                    label, state, reason=why, known_revisions=order
                )
            )
    command.stamp(config, revision)
    connection.commit()
    print(f"stamped {label} at {revision} ({why}) before upgrading to head")
    return revision


def upgrade_to_head(
    target: str, *, keep_baseline_admin: bool = False, stamp: str | None = None
) -> None:
    """Bring an empty, partial or half-migrated test schema to head.

    A bare `alembic upgrade head` cannot build this schema, for three independent
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
    * a schema that already has tables but lost its revision (PA-177) fails on the
      first `CREATE TABLE` with `(1050, "Table 'tenants' already exists")`. Stamp it
      first, using `--stamp` or the table-set inference in `infer_revision()`.

    The guard against migrating something real lives in `resolve_target()`: APP_ENV=test,
    a local host, a test-looking name, differs from DATABASE_URL, and not a protected
    name. The authority only ever covers this one connection.
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
            state = schema_state_from_connection(connection)
            current = state.revision
            revisions = frozenset(
                item.revision for item in ScriptDirectory.from_config(config).walk_revisions()
            )
            authority = CustomerInstallAuthority(connection, label, revisions)
            try:
                config.attributes.update(
                    connection=connection, customer_install_authority=authority
                )
                if current is None and state.application_tables:
                    current = _stamp_if_half_migrated(
                        connection, config, label, stamp=stamp, state=state
                    )
                order = revision_order()
                position = order.index(current) if current in order else -1
                if position < order.index(PRE_BOOTSTRAP):
                    command.upgrade(config, PRE_BOOTSTRAP)
                    connection.commit()
                    current, position = PRE_BOOTSTRAP, order.index(PRE_BOOTSTRAP)
                if position < order.index(IDENTITY) and not system_admin_count(connection):
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
                if position < order.index(HEAD):
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


def schema_state_from_connection(connection) -> SchemaState:
    """`schema_state()` for an open connection (no extra engine)."""

    tables = tuple(inspect(connection).get_table_names())
    missing = tuple(name for name in TEST_SCHEMA_MARKER_TABLES if name not in tables)
    revision = None
    if "alembic_version" in tables:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    return SchemaState(missing_markers=missing, revision=revision, tables=tables)


def check_message(label: str, state: SchemaState, head: str) -> str:
    """`--check` text that matches what a real run would actually do (PA-174/177)."""

    if state.missing_markers and state.is_empty:
        return (
            f"TEST_DATABASE_SCHEMA_MISSING: {label} has no application schema"
            f" (missing table(s): {', '.join(state.missing_markers)};"
            f" {len(state.tables)} tables present). Re-running without --check creates it"
            " (add --yes-i-mean-it in CI)."
        )
    if state.revision is None:
        revision, why = infer_revision(state.application_tables)
        if revision is None:
            return (
                f"TEST_DATABASE_SCHEMA_UNSTAMPED: {label} has"
                f" {len(state.application_tables)} application table(s) but no recorded"
                f" alembic revision ({why}). This script will refuse to guess: either drop"
                " and recreate the disposable database (docker compose -f"
                " deploy/docker-compose.yml --profile test down -v mysql-test) or re-run"
                " without --check with --stamp <revision>."
            )
        return (
            f"TEST_DATABASE_SCHEMA_UNSTAMPED: {label} has"
            f" {len(state.application_tables)} application table(s) but no recorded"
            f" alembic revision ({why}). Re-running without --check stamps it at {revision}"
            " and then migrates to head; pass --stamp <revision> if that guess is wrong."
        )
    return (
        f"TEST_DATABASE_SCHEMA_MISSING: {label} is behind head"
        f" (at {state.revision}, head is {head}; {len(state.tables)} tables present)."
        " Re-running without --check migrates to head (add --yes-i-mean-it in CI)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether the test schema is ready (exit 1 when it is not)",
    )
    parser.add_argument(
        "--yes-i-mean-it",
        action="store_true",
        help="skip the interactive confirmation (required in CI / non-interactive shells)",
    )
    parser.add_argument(
        "--stamp",
        metavar="REVISION",
        help="revision to stamp a schema that has tables but no recorded revision",
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
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="show the full traceback when the migration fails (debugging only)",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        target = resolve_target(settings)
        parsed = make_url(target)
        label = f"{parsed.host}:{parsed.port or 3306}/{parsed.database}"
        if not args.check:
            confirm_target(label, parsed.database or "", assume_yes=args.yes_i_mean_it)
    except PreparationRefused as exc:
        print(f"TEST_DATABASE_REFUSED: {exc}")
        return 2

    try:
        state = schema_state(target)
    except Exception as exc:  # noqa: BLE001 - operator-facing message, not a crash dump
        print(
            f"TEST_DATABASE_UNREACHABLE: cannot connect to {label} ({exc.__class__.__name__})."
            " Start it with: docker compose -f deploy/docker-compose.yml --profile test up -d mysql-test"
        )
        return 2

    head = head_revision()
    if not state.missing_markers and state.revision == head:
        if not args.quiet:
            print(
                f"test schema ready: {label} (revision {state.revision},"
                f" {len(state.tables)} tables)"
            )
        return 0

    if args.check:
        print(check_message(label, state, head))
        return 1

    print(
        f"migrating {label} (before: {len(state.tables)} tables, revision"
        f" {state.revision}, head {head})"
    )
    try:
        upgrade_to_head(
            target, keep_baseline_admin=args.keep_baseline_admin, stamp=args.stamp
        )
    except HalfMigratedSchema as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - the operator needs one readable line
        if args.traceback:
            raise
        print(
            f"TEST_DATABASE_PREPARE_FAILED: {label} could not be migrated"
            f" ({exc.__class__.__name__}: {exc}). Re-run with --traceback for the full"
            " stack; the two usual fixes are a --stamp <revision> when the schema already"
            " has tables, or dropping the disposable database and starting over."
        )
        return 1
    after = schema_state(target)
    if after.missing_markers or after.revision != head:
        print(
            f"TEST_DATABASE_SCHEMA_MISSING: {label} is still not ready after the upgrade"
            f" (revision {after.revision}, head {head},"
            f" missing {', '.join(after.missing_markers) or '-'})"
        )
        return 1
    print(f"test schema ready: {label} (revision {after.revision}, {len(after.tables)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
