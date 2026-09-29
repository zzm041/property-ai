from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.cli import bootstrap_system_admin, delivery_migrate


ROOT = Path(__file__).resolve().parents[3]


def _load_migration(filename: str, module_name: str):
    path = ROOT / "backend" / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delivery_settings(host: str = "mysql") -> SimpleNamespace:
    return SimpleNamespace(
        app_env="staging",
        active_database_url=(
            f"mysql+pymysql://property_delivery:secret@{host}:3306/"
            "property_ai_delivery_check?charset=utf8mb4"
        ),
    )


def _approve_delivery(monkeypatch: pytest.MonkeyPatch, host: str = "mysql") -> None:
    monkeypatch.setenv("PROPERTY_AI_DELIVERY_MIGRATION", "approved")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_DELIVERY_TARGET",
        f"{host}:3306/property_ai_delivery_check",
    )


def test_delivery_cli_guards_accept_only_exact_internal_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _delivery_settings()
    _approve_delivery(monkeypatch)
    monkeypatch.setattr(delivery_migrate, "get_settings", lambda: settings)
    assert delivery_migrate._target()[1] == "mysql:3306/property_ai_delivery_check"

    monkeypatch.setattr(delivery_migrate, "get_settings", lambda: _delivery_settings("db"))
    with pytest.raises(RuntimeError, match="exact configured target"):
        delivery_migrate._target()


def test_bootstrap_guard_does_not_accept_test_or_wrong_delivery_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _delivery_settings()
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PHONE", "+8613900000000")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "delivery-test-42!")
    monkeypatch.setattr(bootstrap_system_admin, "get_settings", lambda: settings)
    _, phone, _ = bootstrap_system_admin._require_delivery_target()
    assert phone == "+8613900000000"

    monkeypatch.setenv("PROPERTY_AI_APPROVE_DELIVERY_TARGET", "mysql:3306/other")
    with pytest.raises(RuntimeError, match="exact delivery database"):
        bootstrap_system_admin._require_delivery_target()


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar(self) -> str:
        return self.value

    def scalar_one(self) -> str:
        return self.value

    def scalars(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        return iter(values)


class _Bind:
    def __init__(self, url: str, result: str) -> None:
        self.engine = SimpleNamespace(url=make_url(url))
        self.result = result

    def execute(self, statement, _parameters=None):
        sql = str(statement)
        if "CONSTRAINT_NAME" in sql:
            return _ScalarResult([
                "uq_user_phones_active_e164",
                "uq_user_phones_active_user",
            ])
        if "SYSTEM_ADMIN" in sql:
            return _ScalarResult(1)
        return _ScalarResult(self.result)


def test_0009_guard_keeps_auth001_and_exact_delivery_branches_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration(
        "20260919_0009_official_auth_identity_dispatch.py", "migration_0009_delivery_test"
    )
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    bind = _Bind(
        "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        "property_ai_delivery_check",
    )
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    module._require_isolated_auth_schema()

    monkeypatch.setenv("PROPERTY_AI_APPROVE_DELIVERY_TARGET", "mysql:3306/other")
    with pytest.raises(RuntimeError, match="explicit delivery target"):
        module._require_isolated_auth_schema()


def test_0014_guard_accepts_exact_delivery_and_rejects_wrong_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration(
        "20260926_0014_identity_merge_sla_versions.py", "migration_0014_delivery_test"
    )
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PROPERTY_AI_APPROVE_0014_DATABASE", "property_ai_delivery_check")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_0014_TARGET", "mysql:3306/property_ai_delivery_check"
    )
    bind = _Bind(
        "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        module.down_revision,
    )
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    module._execution_guard()

    bind.result = "wrong_revision"
    with pytest.raises(RuntimeError, match="requires actual 0012"):
        module._execution_guard()


def test_delivery_compose_includes_runtime_isolation_and_persistence_fixes() -> None:
    compose = (ROOT / "deploy" / "docker-compose.delivery.yml").read_text(encoding="utf-8")
    assert 'redis-cli --no-auth-warning -a \\"$${REDIS_PASSWORD}\\"' in compose
    assert "MINIO_ACCESS_KEY_ID: ${MINIO_ROOT_USER:?set MINIO_ROOT_USER}" in compose
    assert "MINIO_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}" in compose
    assert "beat-volume-init:" in compose
    assert "chown -R 10001:10001 /var/run/property-ai" in compose
    assert "127.0.0.1:${HTTP_HOST_PORT:-8093}:80" in compose
    nginx = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in nginx
    assert nginx.count("proxy_pass http://$api_upstream") == 4


def test_delivery_rag_runtime_is_isolated_bounded_and_not_an_auto_switch() -> None:
    compose = (ROOT / "deploy" / "docker-compose.delivery.yml").read_text(encoding="utf-8")
    runtime = (ROOT / "deploy" / "rag-runtime.ps1").read_text(encoding="utf-8")
    example = (ROOT / "deploy" / ".env.delivery.example").read_text(encoding="utf-8")

    assert "LOCAL_AI_INTERNAL_TOKEN: ${DELIVERY_RAG_INTERNAL_TOKEN:-}" in compose
    assert "RAG_LLM_API_KEY: ${DELIVERY_RAG_INTERNAL_TOKEN:-}" not in compose
    assert '"host.docker.internal:host-gateway"' in compose
    assert 'scope = "rag_only"' in runtime
    assert 'embedding = 12; reranker = 8; qwen = 18' in runtime
    assert 'duration_seconds = 7200' in runtime
    assert "request_active" in runtime
    assert "process start time does not match" in runtime
    assert 'RAG_LLM_PROVIDER=inherit' in example
    assert 'LLM_PROVIDER=development' in example
    assert 'MILVUS_COLLECTION=knowledge_chunks_delivery_check' in example
    assert 'EMBEDDING_DIMENSIONS=8' in example


def test_delivery_work_ai_runtime_is_frozen_scoped_and_resource_guarded() -> None:
    compose=(ROOT/'deploy/docker-compose.delivery.yml').read_text(encoding='utf-8')
    runtime=(ROOT/'deploy/work-ai-runtime.ps1').read_text(encoding='utf-8')
    launcher=(ROOT/'deploy/delivery.ps1').read_text(encoding='utf-8')
    assert 'LLM_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'VLM_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'ASR_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'scope="work_order_only"' in runtime
    assert 'port=11437' in runtime
    assert 'runtime_binding_sha256=' in runtime
    assert 'runtime_binding_sha256=(Get-FileHash' in runtime and '.Hash.ToLowerInvariant()' in runtime
    assert '([DateTimeOffset]$ollama.started_at_utc).UtcDateTime.ToString("o")' in runtime
    assert 'asr_start_ram_mib=11264' in runtime
    assert 'qwen=18' in runtime and 'duration_seconds=7200' in runtime
    assert 'git -C $RepoRoot archive' in runtime
    assert 'adapter-$operationId.stdout.log' in runtime and 'adapter-$operationId.stderr.log' in runtime
    assert '__SOURCE_COMMIT__' not in runtime and '__ADAPTER_SHA256__' not in runtime
    assert '10B1780A98D57E6F7F0A909090117792A960AC7B08910C00BF92742077AC0DD6' in runtime
    assert 'call-001-resolution.json' in runtime
    assert 'PASS_SCOPED_CALL_RESOLUTION' not in runtime
    assert 'resolution_root=$ResolutionRoot' in runtime
    assert 'reservation_sha256=$ExpectedReservationSha256.ToLowerInvariant()' in runtime
    assert 'work-ai-start' in launcher and 'work-ai-status' in launcher and 'work-ai-stop' in launcher


def test_delivery_starts_and_diagnoses_durable_task_publisher() -> None:
    compose=(ROOT/'deploy/docker-compose.delivery.yml').read_text(encoding='utf-8')
    launcher=(ROOT/'deploy/delivery.ps1').read_text(encoding='utf-8')
    example=(ROOT/'deploy/.env.delivery.example').read_text(encoding='utf-8')
    assert 'publisher:' in compose
    assert 'app.worker.task_intent_publisher' in compose
    assert 'DURABLE_TASKS_ENABLED=true' in example
    assert 'up -d api worker publisher beat nginx' in launcher
    assert 'milvus api worker publisher beat nginx' in launcher
    assert 'logs --tail 120 api worker publisher beat nginx' in launcher


def test_delivery_backup_uses_binary_safe_compose_copy() -> None:
    script = (ROOT / "deploy" / "delivery.ps1").read_text(encoding="utf-8")
    assert 'Invoke-Compose cp "mysql:$containerTarget" $hostTemp' in script
    assert "Invoke-Compose cp $dump \"mysql:$containerDump\"" in script
    assert "restore target exists or could not be verified" in script
    assert '"${database}_restore_check_029"' in script
    assert "explicit restore database is not the approved delivery target" in script
    assert "approved restore continuation target is not empty" in script
    assert "backup path must stay inside deploy/backups" in script
    assert "backup target or manifest already exists" in script
    assert "backup target is already reserved by another operation" in script
    assert "[IO.FileMode]::CreateNew" in script
    assert "[IO.FileShare]::None" in script
    assert "[IO.File]::Move($hostTemp, $target)" in script
    assert "[IO.File]::Move($manifestTemp, $manifestPath)" in script
    assert 'format = "property-ai-delivery-backup-v1"' in script
    assert "trusted backup manifest is missing" in script
    assert "backup manifest does not match the dump" in script
    assert "dump contains a forbidden cross-database or global statement" in script
    assert "GRANT ALL PRIVILEGES ON {2}.*" in script
    assert 'exec mysql -u\"$DELIVERY_RESTORE_USER\"' in script
    assert 'exec mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" \'\'{0}\'\'' not in script
