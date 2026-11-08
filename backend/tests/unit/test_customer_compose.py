"""Offline customer Compose rendering with synthetic configuration only."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from app.cli.customer_environment import export


ROOT = Path(__file__).resolve().parents[3]


def render_customer_config(tmp_path, install_profile=False):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose config renderer is required")
    for name in ("docker-compose.delivery.yml", "docker-compose.customer.yml"):
        shutil.copy2(ROOT / "deploy" / name, tmp_path / name)
    template = (ROOT / "deploy/.env.customer.example").read_text(encoding="utf-8")
    env_file = tmp_path / ".env.customer"
    env_file.write_text(template, encoding="utf-8")
    export(env_file, tmp_path / ".env.customer.app")
    # Ordinary operation must work after bootstrap credentials are removed.
    if install_profile == "install":
        (tmp_path / ".env.customer.bootstrap").write_text("BOOTSTRAP_ADMIN_PASSWORD=SYNTHETIC_ONLY\n", encoding="utf-8")
    if install_profile == "storage-install":
        (tmp_path / ".env.customer.storage-bootstrap").write_text("STORAGE_BOOTSTRAP_SECRET_KEY=SYNTHETIC_ONLY\n", encoding="utf-8")
    env = dict(os.environ)
    for line in template.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    args = ["docker", "compose", "--env-file", str(env_file), "-f", str(tmp_path / "docker-compose.delivery.yml"),
            "-f", str(tmp_path / "docker-compose.customer.yml")]
    if install_profile:
        args.extend(["--profile", install_profile])
    result = subprocess.run([*args, "config", "--format", "json"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, "isolated customer Compose config failed (output withheld)"
    return json.loads(result.stdout)


@pytest.mark.parametrize("install_profile", [False, "install", "storage-install"])
def test_customer_project_and_credentials_are_isolated(tmp_path, install_profile):
    config = render_customer_config(tmp_path, install_profile)
    assert config["name"] == "customer-property-ai"
    assert config["volumes"]["mysql_data"]["name"] == "customer-property-ai-mysql-data"
    services = config["services"]
    scanner = services["scanner"]
    assert scanner["image"] == "clamav/clamav:1.4.6-debian13-slim@sha256:ce80e97855d937e93009bb47eb5ecb410b514b844b03d0ecf00e8eeec92810cc"
    assert not scanner.get("ports")
    assert set(scanner["networks"]) == {"scanner"}
    assert scanner["networks"]["scanner"]["ipv4_address"] == "172.31.240.2"
    assert int(scanner["mem_limit"]) == 4 * 1024**3
    assert scanner["environment"]["CLAMAV_NO_FRESHCLAMD"] == "false"
    assert scanner["environment"]["CLAMD_CONF_StreamMaxLength"] == "10M"
    assert scanner["healthcheck"]["test"] == ["CMD", "clamdcheck.sh"]
    assert scanner["volumes"][0]["source"] == "scanner_data"
    assert config["networks"]["scanner"]["ipam"]["config"][0]["subnet"] == "172.31.240.0/28"
    assert config["volumes"]["scanner_data"]["name"].startswith("customer-property-ai")
    for name in ("api", "worker", "publisher", "beat"):
        assert set(services[name]["networks"]) == {"default", "scanner"}
        assert services[name]["depends_on"]["scanner"]["condition"] == "service_healthy"
        assert services[name]["environment"]["FILE_SCANNER_HOST"] == scanner["networks"]["scanner"]["ipv4_address"]
        assert "BOOTSTRAP_ADMIN_PASSWORD" not in services[name]["environment"]
        for key in ("MYSQL_ROOT_PASSWORD", "MINIO_ROOT_PASSWORD", "STORAGE_BOOTSTRAP_SECRET_KEY", "CUSTOMER_PROJECT_NAME"):
            assert key not in services[name]["environment"]
        assert services[name]["environment"]["LLM_PROVIDER"] == "production"
        for key, expected in {"LLM_TIMEOUT_SECONDS": "60", "RAG_LLM_TIMEOUT_SECONDS": "60",
            "VLM_TIMEOUT_SECONDS": "60", "ASR_TIMEOUT_SECONDS": "120",
            "EMBEDDING_TIMEOUT_SECONDS": "60", "RERANKER_TIMEOUT_SECONDS": "30",
            "VLM_DISABLE_THINKING": "true", "VLM_MAX_OUTPUT_TOKENS": "256",
            "RAG_LLM_DISABLE_THINKING": "true", "RAG_LLM_MAX_OUTPUT_TOKENS": "512"}.items():
            assert services[name]["environment"].get(key) == expected
    assert ("installer" in services) == (install_profile == "install")
    assert ("storage-installer" in services) == (install_profile == "storage-install")
    if install_profile == "install":
        assert services["installer"]["entrypoint"] == ["python", "-m", "app.cli.customer_install"]
        assert services["installer"]["environment"]["BOOTSTRAP_ADMIN_PASSWORD"] == "SYNTHETIC_ONLY"
    if install_profile == "storage-install":
        assert services["storage-installer"]["entrypoint"] == ["python", "-m", "app.cli.customer_storage"]
        assert services["storage-installer"]["environment"]["STORAGE_BOOTSTRAP_SECRET_KEY"] == "SYNTHETIC_ONLY"
        assert "BOOTSTRAP_ADMIN_PASSWORD" not in services["storage-installer"]["environment"]


@pytest.mark.parametrize("password", ["synthetic-simple", "synthetic with spaces", "*", "synthetic'\"$;value"])
def test_mysql_healthcheck_passes_password_as_one_literal_argument(tmp_path, password):
    shell = shutil.which("bash")
    if os.name == "nt":
        shell = next((str(path) for path in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe",
            Path(os.environ.get("LOCALAPPDATA", "C:/Users/Default/AppData/Local")) / "Programs/Git/bin/bash.exe",
        ) if path.is_file()), None)
    if not shell:
        pytest.skip("POSIX shell is required for the actual healthcheck argument boundary")
    config = render_customer_config(tmp_path)
    check = config["services"]["mysql"]["healthcheck"]["test"]
    assert check[0] == "CMD-SHELL"
    # Compose config retains $$ escaping; the container receives a single $.
    command = check[1].replace("$$", "$")
    (tmp_path / "-pglob-match").write_text("synthetic glob target", encoding="utf-8")
    # Execute the rendered shell command with a capture stub, never a database.
    capture = """mysqladmin() {
      [ "$#" -eq 6 ] || return 41
      [ "$1" = ping ] && [ "$2" = -h ] && [ "$3" = 127.0.0.1 ] && [ "$4" = -uroot ] || return 42
      shift 4
      [ "$1" = "-p$MYSQL_ROOT_PASSWORD" ] && [ "$2" = --silent ] || return 43
    }
"""
    env = dict(os.environ, MYSQL_ROOT_PASSWORD=password)
    for name in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"):
        env.pop(name, None)
    result = subprocess.run([shell, "--noprofile", "--norc", "-c", capture + command],
                            cwd=tmp_path, env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, "healthcheck split or expanded the synthetic password"
