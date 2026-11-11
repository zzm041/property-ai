"""Day 3 nginx/TLS templates, startup guards, and opsz contract."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

REPO = Path(__file__).resolve().parents[2].parent


def test_day4_nginx_uses_isolated_upstream() -> None:
    text = (REPO / "deploy" / "nginx-day4.conf").read_text(encoding="utf-8")
    compose = (REPO / "deploy" / "docker-compose.day4.yml").read_text(encoding="utf-8")
    assert "api-day4:8000" in text
    assert "proxy_pass http://api:8000" not in text
    assert "api-day4:" in compose
    assert "worker-day4:" in compose
    assert "beat-day4:" in compose
    assert "CELERY_TASK_QUEUE: property_ai.day4" in compose
    assert "MILVUS_COLLECTION: knowledge_chunks_day4" in compose
    assert "property_ai_day4" in compose
    assert "DEV_AUTH_ENABLED: \"true\"" in compose
    assert "APP_ENV: test" in compose


def test_all_compose_targets_run_isolated_celery_beat() -> None:
    expected = {
        "docker-compose.yml": "${CELERY_TASK_QUEUE:-property_ai.development}",
        "docker-compose.prodlike.yml": "property_ai.staging",
        "docker-compose.day4.yml": "property_ai.day4",
    }
    for filename, queue in expected.items():
        compose = (REPO / "deploy" / filename).read_text(encoding="utf-8")
        assert '"beat"' in compose
        assert "app.worker.celery_app.celery_app" in compose
        assert queue in compose
        assert "SLA_SWEEP_INTERVAL_SECONDS" in compose
        assert "NOTIFICATION_RETRY_SWEEP_INTERVAL_SECONDS" in compose


def test_nginx_http_config_has_api_health_and_limits() -> None:
    text = (REPO / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "location /api/" in text
    assert "location = /healthz" in text
    assert "location = /readyz" in text
    assert "client_max_body_size 12m" in text
    assert "proxy_read_timeout 60s" in text
    assert "X-Request-Id" in text
    assert "X-Content-Type-Options nosniff" in text
    assert "listen 443" not in text


def test_nginx_tls_template_is_ready_without_live_certs() -> None:
    text = (REPO / "deploy" / "nginx-tls.conf.example").read_text(encoding="utf-8")
    assert "listen 443 ssl" in text
    assert "ssl_certificate" in text
    assert "ssl_certificate_key" in text
    assert "server_name api.example.com" in text
    assert "Strict-Transport-Security" in text
    assert "BEGIN PRIVATE KEY" not in text


def test_api_and_worker_startup_do_not_migrate_or_seed() -> None:
    main = (REPO / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    celery = (REPO / "backend" / "app" / "worker" / "celery_app.py").read_text(
        encoding="utf-8"
    )
    tasks = (REPO / "backend" / "app" / "worker" / "tasks.py").read_text(encoding="utf-8")
    assert "alembic" not in main
    assert "dev_seed" not in main
    assert "alembic" not in celery
    assert "dev_seed" not in celery
    assert "alembic" not in tasks
    assert "dev_seed" not in tasks


def test_opsz_requires_operator_authentication() -> None:
    settings = Settings(
        _env_file=None,
        app_env="staging",
        dev_auth_enabled=False,
        celery_task_queue="property_ai.staging",
        milvus_collection="knowledge_chunks_staging",
        storage_provider="production",
        storage_endpoint="http://minio:9000",
        storage_bucket="property-ai-staging",
        storage_access_key="minioadmin",
        storage_secret_key="minioadmin",
        llm_provider="development",
        wechat_login_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/opsz")
    assert response.status_code == 401


def test_rebuild_cli_rebuilds_from_mysql_source() -> None:
    source = (REPO / "backend" / "app" / "cli" / "rebuild_knowledge_index.py").read_text(
        encoding="utf-8"
    )
    assert "rebuild=True" in source
    assert "KnowledgeDocument" in source
    assert "ingest_version" in source
