"""Create and verify the exact persistent AUTH001 retry target on port 3317.

The tool is deliberately not a general restore utility.  It accepts one
reviewed 0012 dump, one container/volume pair, and one loopback target.  It
has no cleanup, overwrite, downgrade, or arbitrary SQL mode.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
import site

site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))

from sqlalchemy.engine import make_url

from app.core.config import Settings
from auth001_backup_restore_027 import reject_unsafe_dump

CONTAINER = "property-ai-auth001-retry-3317"
VOLUME = "property-ai-auth001-retry-3317-data"
IMAGE = "mysql@sha256:bf577825b52ab281d6281fb281eabbfdc73507eda8f2c2745790251533ef0306"
HOST = "127.0.0.1"
PORT = 3317
DATABASE = "property_ai_auth001_test"
REVISION = "20260925_0012_task_intents"
REVISION_0014 = "20260926_0014_identity_merge_sla_versions"
SOURCE_DUMP = (
    ROOT
    / "tmp"
    / "auth001-persist-v2"
    / "backups"
    / "baseline-20260927v2"
    / "auth001-0012.sql"
)
DUMP_BYTES = 749_523
DUMP_SHA256 = "c5bf89eeefb272ac7313c59169ddc2e2807ffb854e9db566ce02bff5bf94322f"
STATE = ROOT / "tmp" / "auth001-retry-3317"
SECRETS = STATE / "secrets"
BASELINE = STATE / "baseline"
MANIFEST = STATE / "manifest.json"
ROOT_SECRET = SECRETS / "root-password.txt"
APP_SECRET = SECRETS / "app-password.txt"
COPY_DUMP = BASELINE / "auth001-0012.sql"
COMPATIBILITY_EVIDENCE = STATE / "alembic-version-compatibility.json"
NEW_0014_TABLES = {
    "account_merges",
    "identity_recovery_cases",
    "identity_recovery_reviews",
    "phone_identity_tombstones",
    "sla_policy_versions",
    "work_order_sla_applications",
}
EXPECTED_COUNTS = {
    "users": 15,
    "work_orders": 40,
    "task_intents": 39,
    "work_order_assignments": 36,
    "audit_logs": 754,
}
PHONE_UNIQUES = ["uq_user_phones_active_e164", "uq_user_phones_active_user"]


def _run(args: list[str], *, data: bytes | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=data, capture_output=True, check=False, timeout=timeout)


def _docker_text(args: list[str], *, timeout: int = 60) -> str:
    result = _run(["docker", *args], timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker command failed: {' '.join(args[:3])}")
    return result.stdout.decode("utf-8", errors="strict").strip()


def _source_settings() -> Settings:
    env_file = next((path for path in (BACKEND / ".env", ROOT / ".env") if path.exists()), None)
    return Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )


def _validated_app_identity() -> tuple[str, str]:
    parsed = make_url(_source_settings().test_database_url)
    if (
        parsed.drivername != "mysql+pymysql"
        or parsed.host != HOST
        or parsed.port != 3307
        or parsed.database not in {"property_ai_test", DATABASE}
        or not parsed.username
        or not parsed.password
    ):
        raise RuntimeError("existing test identity is outside the approved loopback boundary")
    return parsed.username, parsed.password


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_dump(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_size != DUMP_BYTES or _hash(path) != DUMP_SHA256:
        raise RuntimeError("approved 0012 dump size or hash mismatch")
    raw = path.read_bytes()
    reject_unsafe_dump(raw)
    return raw


def _port_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, PORT))
        except OSError:
            return False
    return True


def _secret_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"refusing to replace existing secret file: {path.name}")
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def _root_mysql(sql: str, *, database: str | None = None, data: bytes | None = None) -> subprocess.CompletedProcess:
    target = [] if database is None else [f"--database={database}"]
    command = [
        "docker",
        "exec",
        "-i",
        "--env",
        f"AUTH001_SQL={sql}",
        CONTAINER,
        "sh",
        "-c",
        "export MYSQL_PWD=\"$(cat /run/secrets/auth001/root-password.txt)\"; "
        "exec mysql --batch --skip-column-names --user=root "
        + " ".join(target)
        + (" --execute=\"$AUTH001_SQL\"" if data is None else ""),
    ]
    return subprocess.run(command, input=data, capture_output=True, check=False, timeout=300)


def _query(sql: str, *, database: str | None = None) -> list[list[str]]:
    result = _root_mysql(sql, database=database)
    if result.returncode:
        raise RuntimeError("isolated 3317 MySQL verification query failed")
    return [line.split("\t") for line in result.stdout.decode().splitlines() if line]


def _wait_ready(limit_seconds: int = 120) -> None:
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        if _root_mysql("SELECT 1").returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("retry 3317 MySQL did not become ready")


def _column_metadata() -> dict[str, object]:
    row = _query(
        "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLUMN_KEY, "
        "CHARACTER_SET_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA='{DATABASE}' AND TABLE_NAME='alembic_version' "
        "AND COLUMN_NAME='version_num'"
    )
    if len(row) != 1:
        raise RuntimeError("alembic version column shape is unknown")
    values = row[0]
    return {
        "data_type": values[0],
        "length": int(values[1]),
        "nullable": values[2],
        "column_key": values[3],
        "character_set": values[4],
        "collation": values[5],
    }


def _compatibility_ddl(column: dict[str, object]) -> str:
    if (
        column["data_type"] != "varchar"
        or column["nullable"] != "NO"
        or column["column_key"] != "PRI"
        or column["character_set"] not in {"utf8mb3", "utf8mb4"}
        or not str(column["collation"]).startswith(f"{column['character_set']}_")
    ):
        raise RuntimeError("alembic version column guards do not match the reviewed baseline")
    character_set = str(column["character_set"])
    collation = str(column["collation"])
    return (
        "ALTER TABLE `alembic_version` MODIFY COLUMN `version_num` VARCHAR(64) "
        f"CHARACTER SET {character_set} COLLATE {collation} NOT NULL"
    )


def _metadata() -> dict[str, object]:
    revision = _query("SELECT version_num FROM alembic_version", database=DATABASE)[0][0]
    tables = {
        row[0]
        for row in _query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{DATABASE}' AND TABLE_TYPE='BASE TABLE'"
        )
    }
    counts = {
        table: int(_query(f"SELECT COUNT(*) FROM `{table}`", database=DATABASE)[0][0])
        for table in EXPECTED_COUNTS
    }
    phone_uniques = sorted(
        row[0]
        for row in _query(
            "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            f"WHERE CONSTRAINT_SCHEMA='{DATABASE}' AND TABLE_NAME='user_phones' "
            "AND CONSTRAINT_TYPE='UNIQUE' AND CONSTRAINT_NAME IN "
            "('uq_user_phones_active_e164','uq_user_phones_active_user')"
        )
    )
    return {
        "revision": revision,
        "table_count": len(tables),
        "new_0014_tables": sorted(tables & NEW_0014_TABLES),
        "representative_counts": counts,
        "phone_uniques": phone_uniques,
        "server_uuid": _query("SELECT @@server_uuid")[0][0],
        "alembic_version_column": _column_metadata(),
    }


def _assert_clean_0012(metadata: dict[str, object]) -> None:
    if metadata["revision"] != REVISION:
        raise RuntimeError("retry target is not at exact 0012")
    if metadata["table_count"] != 58 or metadata["new_0014_tables"]:
        raise RuntimeError("retry target is not the clean reviewed 58-table baseline")
    if metadata["representative_counts"] != EXPECTED_COUNTS:
        raise RuntimeError("retry target representative counts differ from the approved baseline")
    if metadata["phone_uniques"] != PHONE_UNIQUES:
        raise RuntimeError("phone uniqueness preconditions are missing")


def provision() -> dict[str, object]:
    if not _port_free():
        raise RuntimeError("approved loopback port 3317 is not free")
    if _docker_text(["ps", "-a", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"]):
        raise RuntimeError("retry container name already exists")
    if _docker_text(["volume", "ls", "--filter", f"name=^{VOLUME}$", "--format", "{{.Name}}"]):
        raise RuntimeError("retry volume name already exists")
    if STATE.exists():
        raise RuntimeError("retry runtime state directory already exists")

    raw = _verify_dump(SOURCE_DUMP)
    username, password = _validated_app_identity()
    BASELINE.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(SOURCE_DUMP, COPY_DUMP)
    _verify_dump(COPY_DUMP)
    _secret_file(ROOT_SECRET, secrets.token_urlsafe(36))
    _secret_file(APP_SECRET, password)

    _docker_text(["volume", "create", VOLUME])
    secret_mount = str(SECRETS.resolve()).replace("\\", "/")
    container_id = _docker_text(
        [
            "run",
            "--detach",
            "--name",
            CONTAINER,
            "--restart",
            "unless-stopped",
            "--publish",
            f"{HOST}:{PORT}:3306",
            "--mount",
            f"type=volume,src={VOLUME},dst=/var/lib/mysql",
            "--mount",
            f"type=bind,src={secret_mount},dst=/run/secrets/auth001,readonly",
            "--env",
            "MYSQL_ROOT_PASSWORD_FILE=/run/secrets/auth001/root-password.txt",
            "--env",
            f"MYSQL_DATABASE={DATABASE}",
            "--env",
            f"MYSQL_USER={username}",
            "--env",
            "MYSQL_PASSWORD_FILE=/run/secrets/auth001/app-password.txt",
            IMAGE,
            "--character-set-server=utf8mb4",
            "--collation-server=utf8mb4_0900_ai_ci",
        ],
        timeout=120,
    )
    _wait_ready()
    if int(
        _query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{DATABASE}' AND TABLE_TYPE='BASE TABLE'"
        )[0][0]
    ):
        raise RuntimeError("fresh retry target is not empty")
    restored = _root_mysql("", database=DATABASE, data=raw)
    if restored.returncode:
        raise RuntimeError("approved 0012 dump import failed; retry assets preserved")
    metadata = _metadata()
    _assert_clean_0012(metadata)
    if metadata["alembic_version_column"]["length"] != 32:
        raise RuntimeError("restored version column is not the reviewed VARCHAR(32) baseline")
    result = {
        "task_id": "AUTH001-RETRY-3317",
        "created_utc": datetime.now(UTC).isoformat(),
        "container": CONTAINER,
        "container_id": container_id,
        "image": IMAGE,
        "volume": VOLUME,
        "volume_mode": "named-persistent-volume",
        "target": f"{HOST}:{PORT}/{DATABASE}",
        "source_dump": str(SOURCE_DUMP),
        "baseline_copy": str(COPY_DUMP),
        "dump_bytes": DUMP_BYTES,
        "dump_sha256": DUMP_SHA256,
        "metadata": metadata,
        "old_3307_touched": False,
    }
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _require_recorded_runtime() -> dict[str, object]:
    if not MANIFEST.is_file():
        raise RuntimeError("retry manifest is missing")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("container") != CONTAINER
        or manifest.get("volume") != VOLUME
        or manifest.get("image") != IMAGE
        or manifest.get("target") != f"{HOST}:{PORT}/{DATABASE}"
        or manifest.get("dump_sha256") != DUMP_SHA256
    ):
        raise RuntimeError("retry manifest does not match the exact approved runtime")
    inspected = json.loads(_docker_text(["inspect", CONTAINER]))
    if len(inspected) != 1:
        raise RuntimeError("retry container inspection is ambiguous")
    _validate_runtime_inspect(manifest, inspected[0])
    expected_uuid = ((manifest.get("metadata") or {}).get("server_uuid"))
    if not expected_uuid or _query("SELECT @@server_uuid")[0][0] != expected_uuid:
        raise RuntimeError("live retry server_uuid does not match the recorded fixed asset")
    inspected_after_query = json.loads(_docker_text(["inspect", CONTAINER]))
    if len(inspected_after_query) != 1:
        raise RuntimeError("retry container post-query inspection is ambiguous")
    _validate_runtime_inspect(manifest, inspected_after_query[0])
    return manifest


def _validate_runtime_inspect(manifest: dict[str, object], inspected: dict[str, object]) -> None:
    mounts = inspected.get("Mounts") or []
    volume_mounts = [
        mount
        for mount in mounts
        if mount.get("Type") == "volume" and mount.get("Destination") == "/var/lib/mysql"
    ]
    ports = ((inspected.get("NetworkSettings") or {}).get("Ports") or {}).get("3306/tcp") or []
    if (
        inspected.get("Id") != manifest.get("container_id")
        or (inspected.get("Name") or "").lstrip("/") != CONTAINER
        or (inspected.get("Config") or {}).get("Image") != IMAGE
        or not (inspected.get("State") or {}).get("Running")
        or len(volume_mounts) != 1
        or volume_mounts[0].get("Name") != VOLUME
        or len(ports) != 1
        or ports[0].get("HostIp") != HOST
        or ports[0].get("HostPort") != str(PORT)
    ):
        raise RuntimeError("actual retry container is not the recorded fixed asset")


def restart_verify() -> dict[str, object]:
    _require_recorded_runtime()
    evidence_path = STATE / "restart-verification.json"
    if evidence_path.exists():
        raise RuntimeError("restart verification evidence already exists and will not be overwritten")
    before = _metadata()
    _assert_clean_0012(before)
    _docker_text(["restart", CONTAINER], timeout=120)
    _wait_ready()
    after = _metadata()
    if before != after:
        raise RuntimeError("persistent retry restart changed the restored baseline")
    result = {"verified_utc": datetime.now(UTC).isoformat(), "before": before, "after": after}
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def apply_version_compatibility() -> dict[str, object]:
    _require_recorded_runtime()
    before = _metadata()
    _assert_clean_0012(before)
    version_before = before["revision"]
    column_before = before["alembic_version_column"]
    length = column_before["length"]
    if length == 32:
        if COMPATIBILITY_EVIDENCE.exists():
            raise RuntimeError("compatibility evidence exists before the reviewed ALTER")
        statement = _compatibility_ddl(column_before)
        changed = _root_mysql(statement, database=DATABASE)
        if changed.returncode:
            raise RuntimeError("exact alembic version compatibility ALTER failed")
        applied = True
    elif length == 64:
        _compatibility_ddl(column_before)
        if not COMPATIBILITY_EVIDENCE.is_file():
            raise RuntimeError("VARCHAR(64) exists without the immutable compatibility evidence")
        existing = json.loads(COMPATIBILITY_EVIDENCE.read_text(encoding="utf-8"))
        if (
            existing.get("target") != f"{HOST}:{PORT}/{DATABASE}"
            or existing.get("applied") is not True
            or existing.get("revision_unchanged") != REVISION
            or existing.get("after") != column_before
        ):
            raise RuntimeError("existing compatibility evidence does not match the live target")
        return {
            **existing,
            "reverified_utc": datetime.now(UTC).isoformat(),
            "original_evidence_preserved": True,
        }
    else:
        raise RuntimeError("alembic version column length is neither reviewed baseline nor target")

    after = _metadata()
    _assert_clean_0012(after)
    if version_before != after["revision"] or after["alembic_version_column"] != {
        **column_before,
        "length": 64,
    }:
        raise RuntimeError("compatibility step changed more than the version column capacity")
    before_without_column = {key: value for key, value in before.items() if key != "alembic_version_column"}
    after_without_column = {key: value for key, value in after.items() if key != "alembic_version_column"}
    if before_without_column != after_without_column:
        raise RuntimeError("compatibility step changed baseline metadata")
    result = {
        "task_id": "AUTH001-RETRY-3317-VERSION-COMPATIBILITY",
        "observed_utc": datetime.now(UTC).isoformat(),
        "target": f"{HOST}:{PORT}/{DATABASE}",
        "applied": applied,
        "statement": statement,
        "revision_unchanged": version_before,
        "before": column_before,
        "after": after["alembic_version_column"],
        "baseline_metadata_unchanged": True,
    }
    COMPATIBILITY_EVIDENCE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def backup_post0014(run_id: str) -> dict[str, object]:
    if not run_id.startswith("post0014-") or not run_id.removeprefix("post0014-").isalnum():
        raise RuntimeError("bounded post0014 backup run id required")
    _require_recorded_runtime()
    output = STATE / "backups" / run_id
    if output.exists():
        raise RuntimeError("post0014 backup destination already exists")
    before = _metadata()
    if (
        before["revision"] != REVISION_0014
        or before["table_count"] != 64
        or before["new_0014_tables"] != sorted(NEW_0014_TABLES)
        or before["phone_uniques"] != PHONE_UNIQUES
    ):
        raise RuntimeError("post0014 backup source is not the exact migrated target")
    output.mkdir(parents=True, exist_ok=False)
    command = [
        "docker",
        "exec",
        CONTAINER,
        "sh",
        "-c",
        "export MYSQL_PWD=\"$(cat /run/secrets/auth001/root-password.txt)\"; "
        "exec mysqldump --single-transaction --skip-lock-tables --no-tablespaces "
        "--set-gtid-purged=OFF --skip-add-drop-table --no-create-db --skip-triggers "
        "--hex-blob --default-character-set=utf8mb4 --user=root " + DATABASE,
    ]
    dumped = _run(command, timeout=300)
    incomplete = output / "auth001-0014.sql.incomplete"
    incomplete.write_bytes(dumped.stdout)
    if dumped.returncode:
        raise RuntimeError("post0014 dump failed; partial output retained")
    reject_unsafe_dump(dumped.stdout)
    dump = output / "auth001-0014.sql"
    incomplete.rename(dump)
    after = _metadata()
    if before != after:
        raise RuntimeError("source changed during post0014 backup")
    result = {
        "task_id": "AUTH001-RETRY-3317-POST0014-BACKUP",
        "run_id": run_id,
        "created_utc": datetime.now(UTC).isoformat(),
        "container": CONTAINER,
        "volume": VOLUME,
        "target": f"{HOST}:{PORT}/{DATABASE}",
        "dump_path": str(dump),
        "dump_bytes": dump.stat().st_size,
        "dump_sha256": _hash(dump),
        "before": before,
        "after": after,
    }
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("provision", "restart-verify", "version-compatibility", "backup-post0014"),
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.command == "provision":
        result = provision()
    elif args.command == "restart-verify":
        result = restart_verify()
    elif args.command == "version-compatibility":
        result = apply_version_compatibility()
    else:
        if not args.run_id:
            parser.error("backup-post0014 requires --run-id")
        result = backup_post0014(args.run_id)
    safe = dict(result)
    safe.pop("container_id", None)
    print(json.dumps(safe, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
