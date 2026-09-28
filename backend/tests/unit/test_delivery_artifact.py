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


def test_delivery_backup_uses_binary_safe_compose_copy() -> None:
    script = (ROOT / "deploy" / "delivery.ps1").read_text(encoding="utf-8")
    assert 'Invoke-Compose cp "mysql:$containerTarget" $hostTemp' in script
    assert "Invoke-Compose cp $dump \"mysql:$containerDump\"" in script
    assert "restore target exists or could not be verified" in script
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
    assert "GRANT ALL PRIVILEGES ON `{2}`.*" in script
    assert 'exec mysql -u\"$DELIVERY_RESTORE_USER\"' in script
    assert 'exec mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" \'\'{0}\'\'' not in script
