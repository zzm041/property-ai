"""Provision dedicated persistent RabbitMQ, Redis and MinIO for AUTH001 v2.

The tool uses fresh exact names, loopback-only ports and ignored secret files.
It never connects to or modifies the legacy/shared dependency containers.
"""
from __future__ import annotations

from datetime import UTC, datetime
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "tmp" / "auth001-persist-v2" / "dependencies"
SECRETS = STATE / "secrets"
MANIFEST = STATE / "manifest.json"
NETWORK = "property-ai-auth001-persist-v2-net"
PREFIX = "property-ai-auth001-persist-v2"
RABBIT = f"{PREFIX}-rabbitmq"
REDIS = f"{PREFIX}-redis"
MINIO = f"{PREFIX}-minio"
RABBIT_VOLUME = f"{PREFIX}-rabbitmq-data"
REDIS_VOLUME = f"{PREFIX}-redis-data"
MINIO_VOLUME = f"{PREFIX}-minio-data"
RABBIT_IMAGE = "rabbitmq@sha256:606d8c0d6b3c18d1da9afc53bc7cdb2a8d5486df91b5a9830e9e07626c9ae281"
REDIS_IMAGE = "redis@sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952"
MINIO_IMAGE = "quay.io/minio/minio@sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
PORTS = {"rabbit": 5675, "rabbit_management": 15675, "redis": 6382, "minio": 9002, "minio_console": 9003}
RABBIT_VHOST = "property_ai_auth001_persist_v2"
QUEUE = "property_ai.auth001.persist.v2"
BUCKET = "property-ai-auth001-persist-v2"


def run(args: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, check=False, timeout=timeout)


def docker(args: list[str], *, timeout: int = 90) -> str:
    result = run(["docker", *args], timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"docker operation failed: {' '.join(args[:3])}")
    return result.stdout.decode("utf-8", errors="strict").strip()


def free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def write_secret(name: str, value: str) -> Path:
    path = SECRETS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
    return path


def wait_result(check, label: str, *, seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(2)
    raise RuntimeError(f"{label} did not become ready")


def rabbit_config() -> Path:
    user = (SECRETS / "rabbit-user.txt").read_text(encoding="utf-8")
    password = (SECRETS / "rabbit-password.txt").read_text(encoding="utf-8")
    vhost = (SECRETS / "rabbit-vhost.txt").read_text(encoding="utf-8")
    if user != "auth001_persist_v2" or vhost != RABBIT_VHOST or not password:
        raise RuntimeError("dedicated RabbitMQ secret identity changed")
    path = SECRETS / "rabbitmq.conf"
    content = f"default_user = {user}\ndefault_pass = {password}\ndefault_vhost = {vhost}\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise RuntimeError("existing RabbitMQ config differs")
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
    return path


def run_rabbit_with_config() -> str:
    mount = str(SECRETS.resolve()).replace("\\", "/")
    return docker([
        "run", "--detach", "--name", RABBIT, "--restart", "unless-stopped", "--network", NETWORK,
        "--publish", f"127.0.0.1:{PORTS['rabbit']}:5672", "--publish", f"127.0.0.1:{PORTS['rabbit_management']}:15672",
        "--mount", f"type=volume,src={RABBIT_VOLUME},dst=/var/lib/rabbitmq",
        "--mount", f"type=bind,src={mount}/rabbitmq.conf,dst=/etc/rabbitmq/rabbitmq.conf,readonly",
        RABBIT_IMAGE,
    ], timeout=120)


def write_manifest(ids: dict[str, str], secret_paths: dict[str, Path]) -> dict:
    evidence = {
        "task_id": "AUTH001-PERSIST-V2-DEPENDENCIES",
        "created_utc": datetime.now(UTC).isoformat(),
        "network": NETWORK,
        "containers": {key: {"name": name, "id": ids[key]} for key, name in (("rabbitmq", RABBIT), ("redis", REDIS), ("minio", MINIO))},
        "images": {"rabbitmq": RABBIT_IMAGE, "redis": REDIS_IMAGE, "minio": MINIO_IMAGE},
        "volumes": {"rabbitmq": RABBIT_VOLUME, "redis": REDIS_VOLUME, "minio": MINIO_VOLUME},
        "loopback_ports": PORTS,
        "rabbit_vhost": RABBIT_VHOST,
        "planned_queue": QUEUE,
        "planned_dlq": f"{QUEUE}.dlq",
        "minio_bucket": BUCKET,
        "secret_files": {key: str(path) for key, path in secret_paths.items()},
        "secret_sha256": {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in secret_paths.items()},
        "legacy_services_connected": False,
        "queue_declared_or_consumed": False,
        "bucket_created": False,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    safe = dict(evidence)
    safe["containers"] = {key: {"name": row["name"]} for key, row in evidence["containers"].items()}
    safe.pop("secret_sha256", None)
    return safe


def provision() -> dict:
    if MANIFEST.exists() or SECRETS.exists():
        raise RuntimeError("dependency state already exists; resume must inspect, never reprovision")
    if not all(free(port) for port in PORTS.values()):
        raise RuntimeError("one or more dedicated loopback ports are occupied")
    for name in (RABBIT, REDIS, MINIO):
        if docker(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"]):
            raise RuntimeError(f"dedicated container name already exists: {name}")
    for name in (RABBIT_VOLUME, REDIS_VOLUME, MINIO_VOLUME):
        if docker(["volume", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}"]):
            raise RuntimeError(f"dedicated volume name already exists: {name}")
    if docker(["network", "ls", "--filter", f"name=^{NETWORK}$", "--format", "{{.Name}}"]):
        raise RuntimeError("dedicated network name already exists")

    rabbit_user = "auth001_persist_v2"
    minio_user = "auth001persistv2"
    secret_paths = {
        "rabbit_user": write_secret("rabbit-user.txt", rabbit_user),
        "rabbit_password": write_secret("rabbit-password.txt", secrets.token_urlsafe(36)),
        "rabbit_vhost": write_secret("rabbit-vhost.txt", RABBIT_VHOST),
        "redis_password": write_secret("redis-password.txt", secrets.token_urlsafe(36)),
        "minio_user": write_secret("minio-user.txt", minio_user),
        "minio_password": write_secret("minio-password.txt", secrets.token_urlsafe(36)),
    }
    docker(["network", "create", NETWORK])
    for name in (RABBIT_VOLUME, REDIS_VOLUME, MINIO_VOLUME):
        docker(["volume", "create", name])
    mount = str(SECRETS.resolve()).replace("\\", "/")
    ids = {}
    rabbit_config()
    ids["rabbitmq"] = run_rabbit_with_config()
    ids["redis"] = docker([
        "run", "--detach", "--name", REDIS, "--restart", "unless-stopped", "--network", NETWORK,
        "--publish", f"127.0.0.1:{PORTS['redis']}:6379",
        "--mount", f"type=volume,src={REDIS_VOLUME},dst=/data",
        "--mount", f"type=bind,src={mount},dst=/run/secrets/auth001,readonly",
        REDIS_IMAGE, "sh", "-c", "exec redis-server --appendonly yes --requirepass \"$(cat /run/secrets/auth001/redis-password.txt)\"",
    ], timeout=120)
    ids["minio"] = docker([
        "run", "--detach", "--name", MINIO, "--restart", "unless-stopped", "--network", NETWORK,
        "--publish", f"127.0.0.1:{PORTS['minio']}:9000", "--publish", f"127.0.0.1:{PORTS['minio_console']}:9001",
        "--mount", f"type=volume,src={MINIO_VOLUME},dst=/data",
        "--mount", f"type=bind,src={mount},dst=/run/secrets/auth001,readonly",
        "--env", "MINIO_ROOT_USER_FILE=/run/secrets/auth001/minio-user.txt",
        "--env", "MINIO_ROOT_PASSWORD_FILE=/run/secrets/auth001/minio-password.txt",
        MINIO_IMAGE, "server", "/data", "--console-address", ":9001",
    ], timeout=120)

    wait_result(lambda: run(["docker", "exec", RABBIT, "rabbitmq-diagnostics", "-q", "ping"], timeout=10).returncode == 0, "rabbitmq")
    wait_result(lambda: run(["docker", "exec", REDIS, "sh", "-c", "exec redis-cli --no-auth-warning -a \"$(cat /run/secrets/auth001/redis-password.txt)\" ping"], timeout=10).returncode == 0, "redis")
    def minio_ready() -> bool:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{PORTS['minio']}/minio/health/live", timeout=2) as response:
                return response.status == 200
        except OSError:
            return False
    wait_result(minio_ready, "minio")

    secret_paths["rabbit_config"] = SECRETS / "rabbitmq.conf"
    return write_manifest(ids, secret_paths)


def resume_rabbitmq() -> dict:
    if MANIFEST.exists() or not SECRETS.is_dir():
        raise RuntimeError("resume boundary rejected")
    expected = {REDIS, MINIO, RABBIT}
    actual = set(filter(None, docker(["ps", "-a", "--filter", f"name=^{PREFIX}-", "--format", "{{.Names}}"] ).splitlines()))
    if actual != expected:
        raise RuntimeError("dedicated dependency assets differ from the one failed attempt")
    state = json.loads(docker(["inspect", RABBIT, "--format", "{{json .State}}"] ))
    image = docker(["inspect", RABBIT, "--format", "{{.Image}}"])
    if state.get("ExitCode") != 1 or image != "sha256:606d8c0d6b3c18d1da9afc53bc7cdb2a8d5486df91b5a9830e9e07626c9ae281":
        raise RuntimeError("failed RabbitMQ identity or failure mode changed")
    rabbit_config()
    docker(["stop", "--time", "10", RABBIT], timeout=30)
    docker(["rm", RABBIT], timeout=30)
    rabbit_id = run_rabbit_with_config()
    wait_result(lambda: run(["docker", "exec", RABBIT, "rabbitmq-diagnostics", "-q", "ping"], timeout=10).returncode == 0, "rabbitmq")
    wait_result(lambda: run(["docker", "exec", REDIS, "sh", "-c", "exec redis-cli --no-auth-warning -a \"$(cat /run/secrets/auth001/redis-password.txt)\" ping"], timeout=10).returncode == 0, "redis")
    ids = {
        "rabbitmq": rabbit_id,
        "redis": docker(["inspect", REDIS, "--format", "{{.Id}}"]),
        "minio": docker(["inspect", MINIO, "--format", "{{.Id}}"]),
    }
    paths = {path.stem.replace("-", "_"): path for path in SECRETS.iterdir() if path.is_file()}
    return write_manifest(ids, paths)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("provision", "resume-rabbitmq"))
    args = parser.parse_args()
    print(json.dumps(provision() if args.command == "provision" else resume_rabbitmq(), ensure_ascii=False))
