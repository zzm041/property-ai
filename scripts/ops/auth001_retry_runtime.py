"""Exact host-side runtime settings for the persistent 3317 AUTH001 candidate."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
FIXED_BACKEND = Path(os.environ.get("AUTH001_FIXED_BACKEND", ROOT / "backend")).resolve()
if str(FIXED_BACKEND) not in sys.path:
    sys.path.insert(0, str(FIXED_BACKEND))
import site

site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))

from sqlalchemy.engine import make_url

from app.core.config import Settings

HOST = "127.0.0.1"
DB_PORT = 3317
DATABASE = "property_ai_auth001_test"
API_PORT = 8083
DB_CONTAINER = "property-ai-auth001-retry-3317"
DB_VOLUME = "property-ai-auth001-retry-3317-data"
DB_IMAGE = "mysql@sha256:bf577825b52ab281d6281fb281eabbfdc73507eda8f2c2745790251533ef0306"
DB_STATE = ROOT / "tmp" / "auth001-retry-3317"
DEPENDENCY_STATE = ROOT / "tmp" / "auth001-persist-v2" / "dependencies"
DEPENDENCY_MANIFEST = DEPENDENCY_STATE / "manifest.json"
RABBIT = "property-ai-auth001-persist-v2-rabbitmq"
REDIS = "property-ai-auth001-persist-v2-redis"
MINIO = "property-ai-auth001-persist-v2-minio"
RABBIT_IMAGE = "rabbitmq@sha256:606d8c0d6b3c18d1da9afc53bc7cdb2a8d5486df91b5a9830e9e07626c9ae281"
REDIS_IMAGE = "redis@sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952"
MINIO_IMAGE = "quay.io/minio/minio@sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
RABBIT_PORT = 5675
REDIS_PORT = 6382
MINIO_PORT = 9002
RABBIT_VHOST = "property_ai_auth001_persist_v2"
QUEUE = "property_ai.auth001.persist.v2"
SLA_ACCEPTANCE_QUEUE = f"{QUEUE}.sla.acceptance"
BUCKET = "property-ai-auth001-persist-v2"
RUNTIME_STATE = DB_STATE / "runtime"
PROOF_SECRET = RUNTIME_STATE / "identity-proof-secret.txt"
ASSET_EVIDENCE = RUNTIME_STATE / "assets.json"
DEPENDENCY_RESOLUTION = RUNTIME_STATE / "rabbit-container-id-resolution.json"


def parse_identity_test_clock(settings: Settings, raw_value: str | None) -> datetime | None:
    """Accept a fixed clock only for the exact isolated 3317 proof runtime.

    This is intentionally a host-side test-runner control.  It is never read by
    the product Settings model and cannot be supplied by an HTTP client.
    """
    if raw_value is None or not raw_value.strip():
        return None
    expected_target = f"{HOST}:{DB_PORT}/{DATABASE}"
    parsed = make_url(settings.active_database_url)
    if (
        settings.app_env != "test"
        or settings.identity_test_proof_enabled is not True
        or settings.identity_test_proof_target != expected_target
        or parsed.host != HOST
        or parsed.port != DB_PORT
        or parsed.database != DATABASE
    ):
        raise RuntimeError("identity test clock refused outside the exact isolated 3317 proof runtime")
    try:
        value = datetime.fromisoformat(raw_value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("identity test clock must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("identity test clock must include an explicit timezone")
    return value.astimezone(UTC)


def _docker_inspect(name: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, check=False, timeout=30
    )
    if result.returncode:
        raise RuntimeError(f"dedicated runtime asset is unavailable: {name}")
    rows = json.loads(result.stdout)
    if len(rows) != 1:
        raise RuntimeError(f"dedicated runtime asset is ambiguous: {name}")
    return rows[0]


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_container(
    actual: dict[str, object],
    *,
    expected_id: str,
    name: str,
    image: str,
    container_port: str,
    host_port: int,
) -> None:
    ports = ((actual.get("NetworkSettings") or {}).get("Ports") or {}).get(container_port) or []
    if (
        actual.get("Id") != expected_id
        or (actual.get("Name") or "").lstrip("/") != name
        or (actual.get("Config") or {}).get("Image") != image
        or not (actual.get("State") or {}).get("Running")
        or len(ports) != 1
        or ports[0].get("HostIp") != HOST
        or ports[0].get("HostPort") != str(host_port)
    ):
        raise RuntimeError(f"dedicated runtime asset identity changed: {name}")


def _rabbit_current_id(dependencies: dict[str, object], actual: dict[str, object]) -> str:
    recorded_id = dependencies["containers"]["rabbitmq"]["id"]
    current_id = str(actual.get("Id") or "")
    if current_id == recorded_id:
        return current_id
    if not DEPENDENCY_RESOLUTION.is_file():
        raise RuntimeError("RabbitMQ container id differs without an immutable resolution record")
    resolution = json.loads(DEPENDENCY_RESOLUTION.read_text(encoding="utf-8"))
    legacy_idle_acceptance = resolution.get("identity_and_idle_checks_passed") is True
    preserved_queue_acceptance = (
        resolution.get("identity_checks_passed") is True
        and resolution.get("existing_messages_preserved") is True
        and resolution.get("existing_queue_consumption_forbidden") is True
    )
    if (
        resolution.get("manifest_sha256") != _hash(DEPENDENCY_MANIFEST)
        or resolution.get("recorded_container_id") != recorded_id
        or resolution.get("current_container_id") != current_id
        or resolution.get("cause") != "UNKNOWN"
        or not (legacy_idle_acceptance or preserved_queue_acceptance)
    ):
        raise RuntimeError("RabbitMQ container id resolution does not match current assets")
    return current_id


def validate_runtime_assets(*, include_rabbit: bool = True) -> dict[str, object]:
    db_manifest = json.loads((DB_STATE / "manifest.json").read_text(encoding="utf-8"))
    dependencies = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
    if (
        db_manifest.get("target") != f"{HOST}:{DB_PORT}/{DATABASE}"
        or db_manifest.get("container") != DB_CONTAINER
        or db_manifest.get("volume") != DB_VOLUME
        or db_manifest.get("image") != DB_IMAGE
    ):
        raise RuntimeError("3317 database manifest changed")
    db = _docker_inspect(DB_CONTAINER)
    _validate_container(
        db,
        expected_id=str(db_manifest["container_id"]),
        name=DB_CONTAINER,
        image=DB_IMAGE,
        container_port="3306/tcp",
        host_port=DB_PORT,
    )
    db_mounts = [
        item
        for item in db.get("Mounts") or []
        if item.get("Type") == "volume" and item.get("Destination") == "/var/lib/mysql"
    ]
    if len(db_mounts) != 1 or db_mounts[0].get("Name") != DB_VOLUME:
        raise RuntimeError("3317 database volume changed")

    expected = {
        "redis": (REDIS, REDIS_IMAGE, "6379/tcp", REDIS_PORT),
        "minio": (MINIO, MINIO_IMAGE, "9000/tcp", MINIO_PORT),
    }
    if include_rabbit:
        expected["rabbitmq"] = (RABBIT, RABBIT_IMAGE, "5672/tcp", RABBIT_PORT)
    for key, (name, image, container_port, host_port) in expected.items():
        record = dependencies["containers"][key]
        actual = _docker_inspect(name)
        expected_id = (
            _rabbit_current_id(dependencies, actual) if key == "rabbitmq" else record["id"]
        )
        _validate_container(
            actual,
            expected_id=expected_id,
            name=name,
            image=image,
            container_port=container_port,
            host_port=host_port,
        )
    if (
        dependencies.get("rabbit_vhost") != RABBIT_VHOST
        or dependencies.get("planned_queue") != QUEUE
        or dependencies.get("minio_bucket") != BUCKET
    ):
        raise RuntimeError("dedicated namespace manifest changed")
    for key, path_value in dependencies["secret_files"].items():
        path = Path(path_value)
        if not path.is_file() or _hash(path) != dependencies["secret_sha256"][key]:
            raise RuntimeError(f"dedicated secret file changed: {key}")
    return {
        "observed_utc": datetime.now(UTC).isoformat(),
        "database": {"container": DB_CONTAINER, "id": db_manifest["container_id"], "target": db_manifest["target"]},
        "dependencies": {
            key: {
                "container": value[0],
                "id": (
                    _docker_inspect(value[0])["Id"]
                    if key == "rabbitmq"
                    else dependencies["containers"][key]["id"]
                ),
                "port": value[3],
            }
            for key, value in expected.items()
        },
        "vhost": RABBIT_VHOST if include_rabbit else "BLOCKED_NOT_ACCEPTED",
        "queue": QUEUE if include_rabbit else "BLOCKED_NOT_ACCEPTED",
        "bucket": BUCKET,
    }


def record_rabbit_resolution() -> dict[str, object]:
    if DEPENDENCY_RESOLUTION.exists():
        raise RuntimeError("RabbitMQ resolution record already exists and will not be overwritten")
    dependencies = json.loads(DEPENDENCY_MANIFEST.read_text(encoding="utf-8"))
    actual = _docker_inspect(RABBIT)
    recorded_id = dependencies["containers"]["rabbitmq"]["id"]
    current_id = str(actual.get("Id") or "")
    if not recorded_id or not current_id or recorded_id == current_id:
        raise RuntimeError("RabbitMQ resolution requires one observed container-id mismatch")
    _validate_container(
        actual,
        expected_id=current_id,
        name=RABBIT,
        image=RABBIT_IMAGE,
        container_port="5672/tcp",
        host_port=RABBIT_PORT,
    )
    mounts = actual.get("Mounts") or []
    if not any(
        item.get("Type") == "volume"
        and item.get("Name") == "property-ai-auth001-persist-v2-rabbitmq-data"
        and item.get("Destination") == "/var/lib/rabbitmq"
        for item in mounts
    ):
        raise RuntimeError("RabbitMQ resolution data volume does not match")
    networks = (actual.get("NetworkSettings") or {}).get("Networks") or {}
    if set(networks) != {dependencies["network"]}:
        raise RuntimeError("RabbitMQ resolution network does not match")
    queue_check = subprocess.run(
        [
            "docker", "exec", RABBIT, "rabbitmqctl", "list_queues", "-p", RABBIT_VHOST,
            "--formatter=json", "name", "messages_ready", "messages_unacknowledged", "consumers",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if queue_check.returncode:
        raise RuntimeError("RabbitMQ resolution queue inspection failed")
    queues = json.loads(queue_check.stdout or b"[]")
    if any(
        item.get("name") != QUEUE
        or int(item.get("messages_unacknowledged") or 0) != 0
        or int(item.get("consumers") or 0) != 0
        for item in queues
    ):
        raise RuntimeError("RabbitMQ resolution refused unknown queues or active consumption")
    connection_check = subprocess.run(
        [
            "docker", "exec", RABBIT, "rabbitmqctl", "list_connections",
            "--formatter=json", "name", "user", "vhost", "channels",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if connection_check.returncode:
        raise RuntimeError("RabbitMQ resolution connection inspection failed")
    connections = json.loads(connection_check.stdout or b"[]")
    if connections:
        raise RuntimeError("RabbitMQ resolution refused active connections")
    created = str(actual.get("Created") or "")
    if not created or created >= str(dependencies.get("created_utc") or ""):
        raise RuntimeError("RabbitMQ resolution creation time does not precede the manifest")
    RUNTIME_STATE.mkdir(parents=True, exist_ok=True)
    result = {
        "task_id": "AUTH001-RETRY-3317-RABBIT-ID-RESOLUTION",
        "observed_utc": datetime.now(UTC).isoformat(),
        "manifest_sha256": _hash(DEPENDENCY_MANIFEST),
        "manifest_created_utc": dependencies["created_utc"],
        "actual_created_utc": created,
        "recorded_container_id": recorded_id,
        "current_container_id": current_id,
        "cause": "UNKNOWN",
        "identity_checks_passed": True,
        "existing_messages_preserved": True,
        "existing_queue_consumption_forbidden": True,
        "checks": {
            "image": RABBIT_IMAGE,
            "volume": "property-ai-auth001-persist-v2-rabbitmq-data",
            "network": dependencies["network"],
            "loopback_port": RABBIT_PORT,
            "vhost": RABBIT_VHOST,
            "queues": [
                {
                    "name": item["name"],
                    "messages_ready": int(item.get("messages_ready") or 0),
                    "messages_unacknowledged": 0,
                    "consumers": 0,
                }
                for item in queues
            ],
            "consumers": 0,
            "connections": 0,
            "acceptance_queue": SLA_ACCEPTANCE_QUEUE,
        },
    }
    DEPENDENCY_RESOLUTION.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _source_settings() -> Settings:
    env_file = next((path for path in (ROOT / "backend" / ".env", ROOT / ".env") if path.exists()), None)
    return Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )


def _secret(name: str) -> str:
    path = DEPENDENCY_STATE / "secrets" / name
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"dedicated secret is empty: {name}")
    return value


def database_url(source: Settings) -> str:
    parsed = make_url(source.test_database_url).set(host=HOST, port=DB_PORT, database=DATABASE)
    if (
        parsed.drivername != "mysql+pymysql"
        or parsed.host != HOST
        or parsed.port != DB_PORT
        or parsed.database != DATABASE
        or not parsed.username
        or not parsed.password
    ):
        raise RuntimeError("retry runtime database target was rejected")
    return parsed.render_as_string(hide_password=False)


def build_settings(
    *,
    enable_enqueue: bool = True,
    enable_test_proof: bool = True,
    queue_name: str = QUEUE,
    require_rabbit_identity: bool | None = None,
) -> Settings:
    if queue_name not in {QUEUE, SLA_ACCEPTANCE_QUEUE}:
        raise RuntimeError("retry runtime queue is outside the fixed allowlist")
    if require_rabbit_identity is None:
        require_rabbit_identity = enable_enqueue
    validate_runtime_assets(include_rabbit=require_rabbit_identity)
    source = _source_settings()
    target_url = database_url(source)
    if enable_test_proof and not PROOF_SECRET.is_file():
        raise RuntimeError("identity proof fixture secret has not been prepared")
    proof_secret = PROOF_SECRET.read_text(encoding="utf-8").strip() if enable_test_proof else ""
    rabbit_password = quote(_secret("rabbit-password.txt"), safe="")
    redis_password = quote(_secret("redis-password.txt"), safe="")
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=target_url,
        database_url=source.database_url,
        app_secret_key=source.app_secret_key,
        dev_auth_enabled=False,
        wechat_login_enabled=True,
        wechat_provider="development",
        sms_provider="development",
        api_port=API_PORT,
        jwt_issuer="auth001-retry-3317",
        jwt_audience="auth001-retry-3317",
        llm_provider="development",
        vlm_provider="development",
        asr_provider="development",
        embedding_provider="development",
        reranker_provider="development",
        notification_provider="development",
        storage_provider="production",
        storage_endpoint=f"http://{HOST}:{MINIO_PORT}",
        storage_bucket=BUCKET,
        storage_access_key=_secret("minio-user.txt"),
        storage_secret_key=_secret("minio-password.txt"),
        ai_auto_assign=True,
        ai_enqueue_on_create=enable_enqueue,
        celery_broker_url=(
            f"amqp://auth001_persist_v2:{rabbit_password}@{HOST}:{RABBIT_PORT}/{RABBIT_VHOST}"
        ),
        celery_task_queue=queue_name,
        redis_url=f"redis://:{redis_password}@{HOST}:{REDIS_PORT}/0",
        identity_test_proof_enabled=enable_test_proof,
        identity_test_proof_secret=proof_secret,
        identity_test_proof_target=f"{HOST}:{DB_PORT}/{DATABASE}",
    )
    if make_url(settings.active_database_url).render_as_string(hide_password=True) != make_url(target_url).render_as_string(hide_password=True):
        raise RuntimeError("retry runtime Settings changed the exact database target")
    return settings


def apply_environ(settings: Settings) -> None:
    values = {
        "APP_ENV": "test",
        "ALLOW_DESTRUCTIVE_TEST_DATABASE": "true",
        "TEST_DATABASE_URL": settings.test_database_url,
        "DATABASE_URL": settings.database_url,
        "APP_SECRET_KEY": settings.app_secret_key.get_secret_value(),
        "DEV_AUTH_ENABLED": "false",
        "WECHAT_LOGIN_ENABLED": "true",
        "WECHAT_PROVIDER": "development",
        "SMS_PROVIDER": "development",
        "LLM_PROVIDER": "development",
        "VLM_PROVIDER": "development",
        "ASR_PROVIDER": "development",
        "EMBEDDING_PROVIDER": "development",
        "RERANKER_PROVIDER": "development",
        "NOTIFICATION_PROVIDER": "development",
        "STORAGE_PROVIDER": "production",
        "STORAGE_ENDPOINT": settings.storage_endpoint,
        "STORAGE_BUCKET": settings.storage_bucket,
        "STORAGE_ACCESS_KEY": settings.storage_access_key.get_secret_value(),
        "STORAGE_SECRET_KEY": settings.storage_secret_key.get_secret_value(),
        "AI_AUTO_ASSIGN": "true",
        "AI_ENQUEUE_ON_CREATE": "true" if settings.ai_enqueue_on_create else "false",
        "CELERY_BROKER_URL": settings.celery_broker_url,
        "CELERY_TASK_QUEUE": settings.celery_task_queue,
        "REDIS_URL": settings.redis_url,
        "JWT_ISSUER": settings.jwt_issuer,
        "JWT_AUDIENCE": settings.jwt_audience,
        "IDENTITY_TEST_PROOF_ENABLED": "true" if settings.identity_test_proof_enabled else "false",
        "IDENTITY_TEST_PROOF_SECRET": settings.identity_test_proof_secret.get_secret_value(),
        "IDENTITY_TEST_PROOF_TARGET": settings.identity_test_proof_target,
    }
    os.environ.update(values)


def prepare_runtime_assets() -> dict[str, object]:
    assets = validate_runtime_assets(include_rabbit=False)
    RUNTIME_STATE.mkdir(parents=True, exist_ok=True)
    if not PROOF_SECRET.exists():
        with PROOF_SECRET.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(48))
    if not PROOF_SECRET.read_text(encoding="utf-8").strip():
        raise RuntimeError("identity proof fixture secret is empty")
    import httpx
    from app.provider.storage import ProductionStorageProvider

    client = httpx.Client(timeout=10.0, trust_env=False)
    try:
        ProductionStorageProvider(
            endpoint=f"http://{HOST}:{MINIO_PORT}",
            bucket=BUCKET,
            access_key=_secret("minio-user.txt"),
            secret_key=_secret("minio-password.txt"),
            client=client,
        ).ensure_bucket()
    finally:
        client.close()
    evidence = {
        "task_id": "AUTH001-RETRY-3317-RUNTIME-ASSETS",
        **assets,
        "proof_fixture": "prepared in ignored local secret; value and hash omitted",
        "bucket_created_or_already_present": True,
        "providers": {"analysis": "Development", "identity_proof": "isolated test fixture", "storage": "dedicated MinIO"},
        "rabbitmq_boundary": "BLOCKED_CONTAINER_ID_UNKNOWN_NOT_ACCEPTED",
    }
    ASSET_EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def safe_description(settings: Settings) -> dict[str, object]:
    return {
        "database": f"{HOST}:{DB_PORT}/{DATABASE}",
        "api": f"{HOST}:{API_PORT}",
        "vhost": RABBIT_VHOST,
        "queue": settings.resolved_celery_queue,
        "redis": f"{HOST}:{REDIS_PORT}/0",
        "storage": f"{HOST}:{MINIO_PORT}/{BUCKET}",
        "analysis_provider": settings.llm_provider,
        "identity_proof": "isolated-test" if settings.identity_test_proof_enabled else "unavailable",
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["resolve-rabbit-id"]:
        result = record_rabbit_resolution()
    elif sys.argv[1:] == ["prepare"]:
        result = prepare_runtime_assets()
    else:
        raise SystemExit("usage: auth001_retry_runtime.py {resolve-rabbit-id|prepare}")
    print(json.dumps(result, ensure_ascii=False))
