"""Shared AUTH001 isolated runtime constants. Host-side API/Worker only."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    assert_isolated_auth_database,
)

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

ISOLATED_API_PORT = 8083
ISOLATED_QUEUE = "property_ai.auth001"
ISOLATED_VHOST = "property_ai_auth001"
ISOLATED_BROKER_USER = "auth001"
ISOLATED_BROKER_PASSWORD_ENV = "AUTH001_BROKER_PASSWORD"
ISOLATED_BROKER_PASSWORD_FILE_ENV = "AUTH001_BROKER_PASSWORD_FILE"
DEFAULT_BROKER_PASSWORD_FILE = REPO / "tmp" / "auth001-runtime" / "broker-password.txt"
ISOLATED_REDIS_DB = 12
ISOLATED_STORAGE_DIR = REPO / "tmp" / "auth001-objects"
ISOLATED_MILVUS_COLLECTION = "knowledge_chunks_auth001"
RABBIT_CONTAINER_FILTER = "property-ai-rabbitmq-1"


def isolated_database_url(source: Settings) -> str:
    return (
        make_url(source.test_database_url)
        .set(database=ISOLATED_AUTH_DATABASE)
        .render_as_string(hide_password=False)
    )


def isolated_broker_password() -> str:
    password = os.environ.get(ISOLATED_BROKER_PASSWORD_ENV, "").strip()
    if password:
        return password
    password_file = Path(
        os.environ.get(
            ISOLATED_BROKER_PASSWORD_FILE_ENV,
            str(DEFAULT_BROKER_PASSWORD_FILE),
        )
    )
    try:
        password = password_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Set {ISOLATED_BROKER_PASSWORD_ENV} or create the local secret file "
            f"configured by {ISOLATED_BROKER_PASSWORD_FILE_ENV}"
        ) from exc
    if not password:
        raise RuntimeError("AUTH001 broker password must not be empty")
    return password


def isolated_broker_url() -> str:
    password = quote(isolated_broker_password(), safe="")
    return (
        f"amqp://{ISOLATED_BROKER_USER}:{password}"
        f"@127.0.0.1:5672/{ISOLATED_VHOST}"
    )


def _redis_with_db(url: str, db: int) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{db}", parsed.query, parsed.fragment)
    )


def load_source_settings() -> Settings:
    env_file = next(
        (path for path in (BACKEND / ".env", REPO / ".env") if path.exists()),
        None,
    )
    return Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )


def build_isolated_settings(
    source: Settings | None = None,
    *,
    enable_enqueue: bool = True,
) -> Settings:
    source = source or load_source_settings()
    isolated_url = isolated_database_url(source)
    ISOLATED_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=isolated_url,
        database_url=source.database_url,
        sms_provider="development",
        dev_auth_enabled=False,
        wechat_login_enabled=True,
        wechat_provider="development",
        api_port=ISOLATED_API_PORT,
        app_secret_key=source.app_secret_key,
        jwt_issuer="test-issuer",
        jwt_audience="test-audience",
        llm_provider="development",
        embedding_provider="development",
        reranker_provider="development",
        milvus_uri=source.milvus_uri,
        milvus_collection=ISOLATED_MILVUS_COLLECTION,
        milvus_timeout_seconds=source.milvus_timeout_seconds,
        storage_provider="development",
        notification_provider="development",
        ai_auto_assign=True,
        ai_enqueue_on_create=enable_enqueue,
        celery_broker_url=isolated_broker_url(),
        celery_task_queue=ISOLATED_QUEUE,
        redis_url=_redis_with_db(source.redis_url, ISOLATED_REDIS_DB),
        dev_storage_dir=str(ISOLATED_STORAGE_DIR),
    )
    assert_isolated_auth_database(settings, database_url=isolated_url)
    return settings


def apply_isolated_environ(settings: Settings) -> None:
    """Worker process reads get_settings() from the environment."""

    import os

    os.environ["APP_ENV"] = "test"
    os.environ["ALLOW_DESTRUCTIVE_TEST_DATABASE"] = "true"
    os.environ["TEST_DATABASE_URL"] = settings.test_database_url
    os.environ["DATABASE_URL"] = settings.database_url
    os.environ["APP_SECRET_KEY"] = settings.app_secret_key.get_secret_value()
    os.environ["DEV_AUTH_ENABLED"] = "false"
    os.environ["WECHAT_LOGIN_ENABLED"] = "true"
    os.environ["WECHAT_PROVIDER"] = "development"
    os.environ["SMS_PROVIDER"] = "development"
    os.environ["LLM_PROVIDER"] = "development"
    os.environ["EMBEDDING_PROVIDER"] = "development"
    os.environ["RERANKER_PROVIDER"] = "development"
    os.environ["MILVUS_URI"] = settings.milvus_uri
    os.environ["MILVUS_COLLECTION"] = settings.milvus_collection
    os.environ["MILVUS_TIMEOUT_SECONDS"] = str(settings.milvus_timeout_seconds)
    os.environ["EMBEDDING_DIMENSIONS"] = str(settings.embedding_dimensions)
    os.environ["STORAGE_PROVIDER"] = "development"
    os.environ["AI_AUTO_ASSIGN"] = "true"
    os.environ["AI_ENQUEUE_ON_CREATE"] = "true" if settings.ai_enqueue_on_create else "false"
    os.environ["CELERY_BROKER_URL"] = settings.celery_broker_url
    os.environ["CELERY_TASK_QUEUE"] = settings.celery_task_queue
    os.environ["REDIS_URL"] = settings.redis_url
    os.environ["DEV_STORAGE_DIR"] = settings.dev_storage_dir
    os.environ["JWT_ISSUER"] = settings.jwt_issuer
    os.environ["JWT_AUDIENCE"] = settings.jwt_audience


def describe_isolated_target(settings: Settings) -> str:
    parsed = make_url(settings.test_database_url)
    return (
        f"serving={ISOLATED_AUTH_HOST}:{ISOLATED_AUTH_PORT}/{parsed.database} "
        f"api=127.0.0.1:{ISOLATED_API_PORT}/api/v1 "
        f"queue={settings.celery_task_queue} "
        f"vhost={ISOLATED_VHOST} "
        f"redis_db={ISOLATED_REDIS_DB} "
        f"milvus_collection={settings.milvus_collection} "
        f"ai_auto_assign={settings.ai_auto_assign} "
        f"ai_enqueue_on_create={settings.ai_enqueue_on_create}"
    )
