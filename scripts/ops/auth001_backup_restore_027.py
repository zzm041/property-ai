"""Exact AUTH001@0012 host backup and isolated restore drill.

The dump is private and retained outside Git.  This tool refuses an existing
destination, never connects application processes to the restored schema, and
does not drop, migrate, seed, or clean any database.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import site
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKUP_ROOT = ROOT / "tmp" / "real-ai-integration-027" / "restore"
CONTAINER = "property-ai-mysql-test-1"
SOURCE = "property_ai_auth001_test"
TARGET = "property_ai_auth001_restore_20260925"
REVISION = "20260925_0012_task_intents"


def validate_source_url(database_url: str, app_env: str) -> None:
    url = make_url(database_url)
    if (
        app_env != "test"
        or url.drivername != "mysql+pymysql"
        or url.host != "127.0.0.1"
        or url.port != 3307
        or url.database != SOURCE
    ):
        raise ValueError("restore027 requires exact loopback AUTH001 test source")


def destination(run_id: str, *, root: Path = BACKUP_ROOT) -> Path:
    if not re.fullmatch(r"restore027-[a-z0-9]{8,24}", run_id):
        raise ValueError("new restore027 run marker required")
    root = root.resolve()
    result = (root / run_id).resolve()
    if result.parent != root or result.exists():
        raise ValueError("backup destination must be new and inside restore root")
    return result


def existing_destination(run_id: str, *, root: Path = BACKUP_ROOT) -> Path:
    if not re.fullmatch(r"restore027-[a-z0-9]{8,24}", run_id):
        raise ValueError("existing restore027 run marker required")
    root = root.resolve()
    result = (root / run_id).resolve()
    if result.parent != root or not result.is_dir():
        raise ValueError("existing backup must be inside restore root")
    return result


def docker_mysql_script(sql: str, *, database: str | None = None) -> list[str]:
    target = "" if database is None else f" --database={database}"
    return [
        "docker", "exec", CONTAINER, "sh", "-c",
        "test -n \"$MYSQL_ROOT_PASSWORD\" || exit 21; "
        "export MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\"; "
        f"exec mysql --batch --skip-column-names --user=root{target} --execute={shlex.quote(sql)}",
    ]


def dump_command() -> list[str]:
    return [
        "docker", "exec", CONTAINER, "sh", "-c",
        "test -n \"$MYSQL_ROOT_PASSWORD\" || exit 21; "
        "export MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\"; exec mysqldump "
        "--single-transaction --skip-lock-tables --no-tablespaces "
        "--set-gtid-purged=OFF --skip-add-drop-table --no-create-db "
        "--skip-triggers --hex-blob --default-character-set=utf8mb4 "
        f"--user=root {SOURCE}",
    ]


def root_query(sql: str) -> list[list[str]]:
    result = subprocess.run(
        docker_mysql_script(sql), capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError("container-local verification query failed")
    return [line.split("\t") for line in result.stdout.splitlines() if line]


def reject_unsafe_dump(raw: bytes) -> None:
    text_value = raw.decode("utf-8", errors="strict")
    executable = "\n".join(
        line for line in text_value.splitlines() if not line.lstrip().startswith("--")
    )
    forbidden = (
        r"\bCREATE\s+DATABASE\b",
        r"\bDROP\s+DATABASE\b",
        r"^\s*USE\s+",
        r"\bCREATE\s+(?:DEFINER\s*=.*\s+)?(?:PROCEDURE|FUNCTION|EVENT|TRIGGER)\b",
    )
    if any(re.search(pattern, executable, flags=re.IGNORECASE | re.MULTILINE) for pattern in forbidden):
        raise ValueError("dump contains database switch or global/executable object")
    if b"CREATE TABLE" not in raw or b"Dump completed" not in raw:
        raise ValueError("dump is incomplete")


def _metadata(connection, schema: str) -> dict:
    revision = connection.execute(
        text(f"SELECT version_num FROM `{schema}`.alembic_version")
    ).scalar_one()
    tables = connection.execute(text(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=:schema AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
    ), {"schema": schema}).scalars().all()
    counts = {
        table: connection.execute(text(f"SELECT COUNT(*) FROM `{schema}`.`{table}`")).scalar_one()
        for table in tables
    }
    shape_rows = connection.execute(text(
        "SELECT 'COLUMN',TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COALESCE(COLUMN_DEFAULT,'<NULL>') "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=:schema "
        "UNION ALL SELECT 'CONSTRAINT',TABLE_NAME,CONSTRAINT_NAME,CONSTRAINT_TYPE,'','' "
        "FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA=:schema "
        "UNION ALL SELECT 'INDEX',TABLE_NAME,INDEX_NAME,CAST(NON_UNIQUE AS CHAR),'','' "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=:schema "
        "ORDER BY 1,2,3,4"
    ), {"schema": schema}).all()
    shape = hashlib.sha256(json.dumps([list(row) for row in shape_rows], ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    representative = {
        "task_intents": counts.get("task_intents", 0),
        "assignments": counts.get("work_order_assignments", 0),
        "contact_phone_rows": connection.execute(text(
            f"SELECT COUNT(*) FROM `{schema}`.work_orders WHERE contact_phone_e164 IS NOT NULL"
        )).scalar_one(),
        "audit_logs": counts.get("audit_logs", 0),
        "resident_relations": counts.get("resident_house_relations", 0),
    }
    return {"revision": revision, "tables": len(tables), "counts": counts,
            "schema_shape_sha256": shape, "representative": representative}


def _metadata_via_container(schema: str) -> dict:
    if schema not in {SOURCE, TARGET}:
        raise ValueError("metadata schema is outside approved pair")
    revision = root_query(f"SELECT version_num FROM `{schema}`.alembic_version")[0][0]
    tables = [row[0] for row in root_query(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
    )]
    union = " UNION ALL ".join(
        f"SELECT '{table}',COUNT(*) FROM `{schema}`.`{table}`" for table in tables
    )
    counts = {row[0]: int(row[1]) for row in root_query(union)} if union else {}
    shape_rows = root_query(
        "SELECT 'COLUMN',TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COALESCE(COLUMN_DEFAULT,'<NULL>') "
        f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='{schema}' "
        "UNION ALL SELECT 'CONSTRAINT',TABLE_NAME,CONSTRAINT_NAME,CONSTRAINT_TYPE,'','' "
        f"FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='{schema}' "
        "UNION ALL SELECT 'INDEX',TABLE_NAME,INDEX_NAME,CAST(NON_UNIQUE AS CHAR),'','' "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='{schema}' ORDER BY 1,2,3,4"
    )
    shape = hashlib.sha256(json.dumps(shape_rows, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    contact_count = int(root_query(
        f"SELECT COUNT(*) FROM `{schema}`.work_orders WHERE contact_phone_e164 IS NOT NULL"
    )[0][0])
    return {
        "revision": revision,
        "tables": len(tables),
        "counts": counts,
        "schema_shape_sha256": shape,
        "representative": {
            "task_intents": counts.get("task_intents", 0),
            "assignments": counts.get("work_order_assignments", 0),
            "contact_phone_rows": contact_count,
            "audit_logs": counts.get("audit_logs", 0),
            "resident_relations": counts.get("resident_house_relations", 0),
        },
    }


def run(database_url: str, app_env: str, run_id: str, *, resume_existing_backup: bool = False,
        resume_verification: bool = False) -> dict:
    validate_source_url(database_url, app_env)
    if resume_existing_backup and resume_verification:
        raise ValueError("choose one bounded resume mode")
    output = (
        existing_destination(run_id)
        if resume_existing_backup or resume_verification
        else destination(run_id)
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            # The application account is intentionally not granted to the
            # restore schema, so existence must be checked with the existing
            # container-local administrative channel.
            target_exists = int(root_query(
                f"SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{TARGET}'"
            )[0][0])
            if target_exists and not resume_verification:
                raise ValueError("approved restore target already exists")
            if not target_exists and resume_verification:
                raise ValueError("verification resume requires this batch target")
            source_before = _metadata(connection, SOURCE)
            if source_before["revision"] != REVISION:
                raise ValueError("source revision is not approved0012")
            object_counts = connection.execute(text(
                "SELECT ROUTINE_TYPE,COUNT(*) FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA=:source GROUP BY ROUTINE_TYPE"
            ), {"source": SOURCE}).all()
            triggers = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=:source"
            ), {"source": SOURCE}).scalar_one()
            events = connection.execute(text(
                "SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=:source"
            ), {"source": SOURCE}).scalar_one()
            if object_counts or triggers or events:
                raise ValueError("source has routines, triggers or events outside this drill")
    finally:
        engine.dispose()

    baseline_path = output / "source-before.json"
    original_source_before = None
    if resume_existing_backup or resume_verification:
        if baseline_path.is_file():
            original_source_before = json.loads(baseline_path.read_text(encoding="utf-8"))
    else:
        output.mkdir(parents=True, exist_ok=False)
        baseline_path.write_text(
            json.dumps(source_before, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        original_source_before = source_before

    dump = output / "auth001-0012.sql"
    if resume_existing_backup or resume_verification:
        if not dump.is_file() or (output / "auth001-0012.sql.incomplete").exists() or (output / "manifest.json").exists():
            raise ValueError("existing backup is not the exact resumable failed-create state")
        raw = dump.read_bytes()
        reject_unsafe_dump(raw)
    else:
        incomplete = output / "auth001-0012.sql.incomplete"
        with incomplete.open("xb") as stream:
            dumped = subprocess.run(dump_command(), stdout=stream, stderr=subprocess.PIPE,
                                    check=False, timeout=180)
        if dumped.returncode != 0:
            raise RuntimeError(f"dump failed exit={dumped.returncode}; partial retained")
        raw = incomplete.read_bytes()
        reject_unsafe_dump(raw)
        incomplete.rename(dump)

    if not resume_verification:
        created = subprocess.run(docker_mysql_script(
            f"CREATE DATABASE `{TARGET}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        ), capture_output=True, check=False, timeout=30)
        if created.returncode != 0:
            raise RuntimeError("target create failed; dump retained")
        restored = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "sh", "-c",
             "test -n \"$MYSQL_ROOT_PASSWORD\" || exit 21; "
             f"export MYSQL_PWD=\"$MYSQL_ROOT_PASSWORD\"; exec mysql --user=root --database={TARGET}"],
            input=raw, capture_output=True, check=False, timeout=300,
        )
        if restored.returncode != 0:
            raise RuntimeError("restore failed; dump and partial target retained")

    source_after = _metadata_via_container(SOURCE)
    target_meta = _metadata_via_container(TARGET)
    if source_after != target_meta:
        raise RuntimeError("restore verification mismatch; all assets retained")
    if original_source_before is not None and original_source_before != source_after:
        raise RuntimeError("source changed since its saved pre-restore snapshot")

    result = {
        "task_id": "PRODUCTION-V1-R6-RESTORE",
        "run_id": run_id,
        "observed_utc": datetime.now(UTC).isoformat(),
        "source": f"127.0.0.1:3307/{SOURCE}@{REVISION}",
        "target": f"127.0.0.1:3307/{TARGET}@{REVISION}",
        "dump_path": str(dump),
        "dump_bytes": len(raw),
        "dump_sha256": hashlib.sha256(raw).hexdigest(),
        "source_before": original_source_before,
        "source_before_status": (
            "CAPTURED_BEFORE_DUMP"
            if original_source_before is not None
            else "UNAVAILABLE_FOR_LEGACY_RESUME"
        ),
        "verification_time_source": source_after,
        "source_after": source_after,
        "restored": target_meta,
        "application_connected_to_restore": False,
        "application_credentials_granted_to_restore": False,
        "verification_method": "container-local root read only; application account intentionally ungranted",
        "resumed_after": (
            "application-account verification was denied after successful create/import"
            if resume_verification else None
        ),
        "media_bytes_restored": False,
        "production_dr": False,
        "retention": "source, target and host backup retained; no cleanup",
    }
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    from auth001_isolated_runtime import build_isolated_settings
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-existing-backup", action="store_true")
    parser.add_argument("--resume-verification", action="store_true")
    args = parser.parse_args()
    settings = build_isolated_settings(enable_enqueue=False)
    result = run(
        settings.active_database_url,
        settings.app_env,
        args.run_id,
        resume_existing_backup=args.resume_existing_backup,
        resume_verification=args.resume_verification,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
