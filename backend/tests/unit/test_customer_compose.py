"""Offline customer Compose rendering with synthetic configuration only."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("install_profile", [False, True])
def test_customer_project_and_credentials_are_isolated(tmp_path, install_profile):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose config renderer is required")
    for name in ("docker-compose.delivery.yml", "docker-compose.customer.yml"):
        shutil.copy2(ROOT / "deploy" / name, tmp_path / name)
    template = (ROOT / "deploy/.env.customer.example").read_text(encoding="utf-8")
    env_file = tmp_path / ".env.customer"
    env_file.write_text(template, encoding="utf-8")
    # Ordinary operation must work after bootstrap credentials are removed.
    if install_profile:
        (tmp_path / ".env.customer.bootstrap").write_text("BOOTSTRAP_ADMIN_PASSWORD=SYNTHETIC_ONLY\n", encoding="utf-8")
    env = dict(os.environ)
    for line in template.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    args = ["docker", "compose", "--env-file", str(env_file), "-f", str(tmp_path / "docker-compose.delivery.yml"),
            "-f", str(tmp_path / "docker-compose.customer.yml")]
    if install_profile:
        args.extend(["--profile", "install"])
    result = subprocess.run([*args, "config", "--format", "json"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, "isolated customer Compose config failed (output withheld)"
    config = json.loads(result.stdout)
    assert config["name"] == "customer-property-ai"
    assert config["volumes"]["mysql_data"]["name"] == "customer-property-ai-mysql-data"
    services = config["services"]
    for name in ("api", "worker", "publisher", "beat"):
        assert "BOOTSTRAP_ADMIN_PASSWORD" not in services[name]["environment"]
        assert services[name]["environment"]["LLM_PROVIDER"] == "production"
        for key, expected in {"LLM_TIMEOUT_SECONDS": "60", "RAG_LLM_TIMEOUT_SECONDS": "60",
            "VLM_TIMEOUT_SECONDS": "60", "ASR_TIMEOUT_SECONDS": "120",
            "EMBEDDING_TIMEOUT_SECONDS": "60", "RERANKER_TIMEOUT_SECONDS": "30",
            "VLM_DISABLE_THINKING": "true", "VLM_MAX_OUTPUT_TOKENS": "256",
            "RAG_LLM_DISABLE_THINKING": "true", "RAG_LLM_MAX_OUTPUT_TOKENS": "512"}.items():
            assert services[name]["environment"].get(key) == expected
    assert ("installer" in services) == install_profile
    if install_profile:
        assert services["installer"]["entrypoint"] == ["python", "-m", "app.cli.customer_install"]
        assert services["installer"]["environment"]["BOOTSTRAP_ADMIN_PASSWORD"] == "SYNTHETIC_ONLY"
