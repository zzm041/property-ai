"""Render an isolated Compose config; never inspect real env files or start services."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("mode", ["provider", "legacy", "empty", "bootstrap_removed", "bootstrap_init", "bootstrap_missing_init"])
def test_compose_preserves_provider_credentials_and_legacy_fallback(tmp_path, mode):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose config renderer is required")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy2(ROOT / "deploy/docker-compose.delivery.yml", deploy / "compose.yml")
    shutil.copy2(ROOT / "deploy/docker-compose.bootstrap.yml", deploy / "bootstrap.yml")
    settings = (ROOT / "deploy/.env.delivery.example").read_text(encoding="utf-8")
    (deploy / ".env.delivery").write_text(settings, encoding="utf-8")
    if mode not in {"bootstrap_removed", "bootstrap_missing_init"}:
        (deploy / ".env.delivery.bootstrap").write_text("BOOTSTRAP_SYSTEM_ADMIN_PASSWORD=SYNTHETIC_INIT_ONLY\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(DELIVERY_ENV_FILE=str(deploy / ".env.delivery"),
               DELIVERY_BOOTSTRAP_ENV_FILE=str(deploy / ".env.delivery.bootstrap"))
    expected = {}
    for key in ("LLM_API_KEY", "VLM_API_KEY", "ASR_API_KEY", "LOCAL_AI_INTERNAL_TOKEN"):
        env[key] = "SYNTHETIC_" + key if mode == "provider" else ""
        expected[key] = env[key] if mode == "provider" else ("SYNTHETIC_LEGACY" if mode == "legacy" else "")
    env["DELIVERY_WORK_AI_INTERNAL_TOKEN"] = "SYNTHETIC_LEGACY" if mode in {"provider", "legacy"} else ""
    env["DELIVERY_RAG_INTERNAL_TOKEN"] = env["DELIVERY_WORK_AI_INTERNAL_TOKEN"]
    overlay = ["-f", str(deploy / "bootstrap.yml")] if mode in {"bootstrap_init", "bootstrap_missing_init"} else []
    result = subprocess.run(["docker", "compose", "--env-file", str(deploy / ".env.delivery"),
                             "-f", str(deploy / "compose.yml"), *overlay, "--profile", "tools", "config", "--format", "json"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    if mode == "bootstrap_missing_init":
        assert result.returncode != 0
        assert ".env.delivery.bootstrap" in result.stderr
        return
    assert result.returncode == 0, "isolated Compose rendering failed (config output withheld)"
    services = json.loads(result.stdout)["services"]
    for service in ("api", "worker", "publisher", "beat"):
        assert "BOOTSTRAP_SYSTEM_ADMIN_PASSWORD" not in services[service]["environment"]
        for key, value in expected.items():
            assert services[service]["environment"][key] == value, f"{service} lost {key} in {mode} mode"
    migration_env = services["migrate"]["environment"]
    assert (migration_env.get("BOOTSTRAP_SYSTEM_ADMIN_PASSWORD") == "SYNTHETIC_INIT_ONLY") == (mode == "bootstrap_init")
