"""Shared AUTH001 isolated runtime constants. Host-side API/Worker only."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
ISOLATED_BROKER_PASSWORD = "change-me-auth001"
ISOLATED_REDIS_DB = 12
ISOLATED_STORAGE_DIR = REPO / "tmp" / "auth001-objects"
RABBIT_CONTAINER_FILTER = "property-ai-rabbitmq-1"


def isolated_database_url(source: Settings) -> str:
    return (
        make_url(source.test_database_url)
        .set(database=ISOLATED_AUTH_DATABASE)
        .render_as_string(hide_password=False)
    )


def isolated_broker_url() -> str:
    return (
        f"amqp://{ISOLATED_BROKER_USER}:{ISOLATED_BROKER_PASSWORD}"
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
        app_secret_key="auth001-isolated-secret-not-production",
        jwt_issuer="test-issuer",
        jwt_audience="test-audience",
        llm_provider="development",
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
        f"ai_auto_assign={settings.ai_auto_assign} "
        f"ai_enqueue_on_create={settings.ai_enqueue_on_create}"
    )
