"""Provision and verify the persistent AUTH001 v2 recovery environment.

This tool never starts, removes, or mutates the legacy mysql-test container.
Secrets stay in ignored host files and are mounted read-only into the new
container.  It accepts only the reviewed 0012 dump and exact database name.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
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

CONTAINER = "property-ai-auth001-persist-v2"
VOLUME = "property-ai-auth001-persist-v2-data"
IMAGE = "mysql@sha256:bf577825b52ab281d6281fb281eabbfdc73507eda8f2c2745790251533ef0306"
DATABASE = "property_ai_auth001_test"
REVISION = "20260925_0012_task_intents"
DUMP_SHA256 = "49a4273a77f8298d2ced2c7fd4a4f1f868b82972950dffeaebc436ec2d826c0c"
DUMP_BYTES = 749_523
SOURCE_DUMP = ROOT / "tmp" / "real-ai-integration-027" / "restore" / "restore027-20260925r6" / "auth001-0012.sql"
STATE = ROOT / "tmp" / "auth001-persist-v2"
SECRETS = STATE / "secrets"
BASELINE = STATE / "baseline"
MANIFEST = STATE / "manifest.json"
ROOT_SECRET = SECRETS / "root-password.txt"
APP_SECRET = SECRETS / "app-password.txt"
COPY_DUMP = BASELINE / "auth001-0012.sql"


def _run(args: list[str], *, data: bytes | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=data, capture_output=True, check=False, timeout=timeout)


def _docker_text(args: list[str], *, timeout: int = 60) -> str:
    result = _run(["docker", *args], timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker command failed: {' '.join(args[:3])}")
    return result.stdout.decode("utf-8", errors="strict").strip()


def _source_settings() -> Settings:
    env_file = next((p for p in (BACKEND / ".env", ROOT / ".env") if p.exists()), None)
    return Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )


def _validated_app_identity() -> tuple[str, str]:
    parsed = make_url(_source_settings().test_database_url)
    if (
        parsed.drivername != "mysql+pymysql"
        or parsed.host != "127.0.0.1"
        or parsed.port != 3307
        or parsed.database not in {"property_ai_test", DATABASE}
        or not parsed.username
        or not parsed.password
    ):
        raise RuntimeError("existing test database identity is outside the approved loopback boundary")
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


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
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
        "docker", "exec", "-i", "--env", f"AUTH001_SQL={sql}", CONTAINER, "sh", "-c",
        "export MYSQL_PWD=\"$(cat /run/secrets/auth001/root-password.txt)\"; "
        "exec mysql --batch --skip-column-names --user=root "
        + " ".join(target)
        + (" --execute=\"$AUTH001_SQL\"" if data is None else ""),
    ]
    return subprocess.run(command, input=data, capture_output=True, check=False, timeout=300)


def _query(sql: str, *, database: str | None = None) -> list[list[str]]:
    result = _root_mysql(sql, database=database)
    if result.returncode:
        raise RuntimeError("isolated MySQL verification query failed")
    return [line.split("\t") for line in result.stdout.decode().splitlines() if line]


def _wait_ready(limit_seconds: int = 120) -> None:
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        result = _root_mysql("SELECT 1")
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("persistent AUTH001 MySQL did not become ready")


def _metadata() -> dict:
    revision = _query("SELECT version_num FROM alembic_version", database=DATABASE)[0][0]
    table_count = int(_query(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{DATABASE}' AND TABLE_TYPE='BASE TABLE'"
    )[0][0])
    server_uuid = _query("SELECT @@server_uuid")[0][0]
    counts = {}
    for table in ("users", "work_orders", "task_intents", "work_order_assignments", "audit_logs"):
        counts[table] = int(_query(f"SELECT COUNT(*) FROM `{table}`", database=DATABASE)[0][0])
    return {
        "revision": revision,
        "table_count": table_count,
        "representative_counts": counts,
        "server_uuid": server_uuid,
    }


def provision(port: int) -> dict:
    if port not in {3307, 3317} or not _port_free(port):
        raise RuntimeError("approved loopback port is not free")
    if _docker_text(["ps", "-a", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"]):
        raise RuntimeError("new persistent container name already exists")
    if _docker_text(["volume", "ls", "--filter", f"name=^{VOLUME}$", "--format", "{{.Name}}"]):
        raise RuntimeError("new persistent volume name already exists")
    if STATE.exists():
        raise RuntimeError("new persistent runtime state directory already exists")

    raw = _verify_dump(SOURCE_DUMP)
    username, password = _validated_app_identity()
    BASELINE.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(SOURCE_DUMP, COPY_DUMP)
    if _verify_dump(COPY_DUMP) != raw:
        raise RuntimeError("host baseline copy differs from approved source")
    _secret_file(ROOT_SECRET, secrets.token_urlsafe(36))
    _secret_file(APP_SECRET, password)

    _docker_text(["volume", "create", VOLUME])
    mount_secrets = str(SECRETS.resolve()).replace("\\", "/")
    try:
        container_id = _docker_text([
            "run", "--detach", "--name", CONTAINER, "--restart", "unless-stopped",
            "--publish", f"127.0.0.1:{port}:3306",
            "--mount", f"type=volume,src={VOLUME},dst=/var/lib/mysql",
            "--mount", f"type=bind,src={mount_secrets},dst=/run/secrets/auth001,readonly",
            "--env", "MYSQL_ROOT_PASSWORD_FILE=/run/secrets/auth001/root-password.txt",
            "--env", f"MYSQL_DATABASE={DATABASE}",
            "--env", f"MYSQL_USER={username}",
            "--env", "MYSQL_PASSWORD_FILE=/run/secrets/auth001/app-password.txt",
            IMAGE,
            "--character-set-server=utf8mb4", "--collation-server=utf8mb4_0900_ai_ci",
        ], timeout=120)
        _wait_ready()
        tables = int(_query(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{DATABASE}' AND TABLE_TYPE='BASE TABLE'"
        )[0][0])
        if tables != 0:
            raise RuntimeError("fresh recovery target is not empty")
        restored = _root_mysql("", database=DATABASE, data=raw)
        if restored.returncode:
            raise RuntimeError("approved 0012 dump import failed; assets preserved")
        metadata = _metadata()
        if metadata["revision"] != REVISION:
            raise RuntimeError("restored database is not at approved 0012")
        result = {
            "task_id": "AUTH001-PERSIST-V2",
            "created_utc": datetime.now(UTC).isoformat(),
            "container": CONTAINER,
            "container_id": container_id,
            "image": IMAGE,
            "volume": VOLUME,
            "volume_mode": "named-persistent-volume",
            "host": "127.0.0.1",
            "port": port,
            "database": DATABASE,
            "source_dump": str(SOURCE_DUMP),
            "baseline_copy": str(COPY_DUMP),
            "dump_bytes": DUMP_BYTES,
            "dump_sha256": DUMP_SHA256,
            "metadata": metadata,
            "legacy_container_touched": False,
        }
        MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception:
        raise


def restart_verify() -> dict:
    if not MANIFEST.is_file():
        raise RuntimeError("provision manifest is missing")
    before = _metadata()
    _docker_text(["restart", CONTAINER], timeout=120)
    _wait_ready()
    after = _metadata()
    if before != after:
        raise RuntimeError("persistent restart changed the restored baseline")
    result = {"verified_utc": datetime.now(UTC).isoformat(), "before": before, "after": after}
    path = STATE / "restart-verification.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def backup(run_id: str) -> dict:
    if not re.fullmatch(r"baseline-[a-z0-9]{8,24}", run_id):
        raise RuntimeError("bounded baseline run id required")
    output = STATE / "backups" / run_id
    if output.exists():
        raise RuntimeError("backup destination already exists")
    before = _metadata()
    if before["revision"] != REVISION:
        raise RuntimeError("backup source is not the approved 0012 baseline")
    output.mkdir(parents=True, exist_ok=False)
    command = [
        "docker", "exec", CONTAINER, "sh", "-c",
        "export MYSQL_PWD=\"$(cat /run/secrets/auth001/root-password.txt)\"; "
        "exec mysqldump --single-transaction --skip-lock-tables --no-tablespaces "
        "--set-gtid-purged=OFF --skip-add-drop-table --no-create-db --skip-triggers "
        "--hex-blob --default-character-set=utf8mb4 --user=root " + DATABASE,
    ]
    dumped = _run(command, timeout=300)
    incomplete = output / "auth001-0012.sql.incomplete"
    incomplete.write_bytes(dumped.stdout)
    if dumped.returncode:
        raise RuntimeError("new baseline dump failed; partial output retained")
    reject_unsafe_dump(dumped.stdout)
    dump = output / "auth001-0012.sql"
    incomplete.rename(dump)
    after = _metadata()
    if before != after:
        raise RuntimeError("source changed during baseline backup")
    result = {
        "task_id": "AUTH001-PERSIST-V2-BASELINE-BACKUP",
        "run_id": run_id,
        "created_utc": datetime.now(UTC).isoformat(),
        "container": CONTAINER,
        "volume": VOLUME,
        "database": DATABASE,
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
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("provision")
    create.add_argument("--port", type=int, choices=(3307, 3317), default=3307)
    sub.add_parser("restart-verify")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "provision":
        result = provision(args.port)
    elif args.command == "restart-verify":
        result = restart_verify()
    else:
        result = backup(args.run_id)
    safe = dict(result)
    safe.pop("container_id", None)
    print(json.dumps(safe, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
