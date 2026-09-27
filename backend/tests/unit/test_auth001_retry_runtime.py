from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ops" / "auth001_retry_runtime.py"


def _module():
    spec = importlib.util.spec_from_file_location("auth001_retry_runtime", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retry_runtime_namespaces_are_exact_and_loopback_only() -> None:
    module = _module()
    assert (module.HOST, module.DB_PORT, module.DATABASE) == (
        "127.0.0.1", 3317, "property_ai_auth001_test"
    )
    assert module.RABBIT_PORT == 5675
    assert module.REDIS_PORT == 6382
    assert module.MINIO_PORT == 9002
    assert module.RABBIT_VHOST == "property_ai_auth001_persist_v2"
    assert module.QUEUE == "property_ai.auth001.persist.v2"
    assert module.BUCKET == "property-ai-auth001-persist-v2"


def test_container_guard_rejects_public_or_drifted_assets() -> None:
    module = _module()
    actual = {
        "Id": "a" * 64,
        "Name": "/fixed",
        "Config": {"Image": "image@sha256:fixed"},
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3317"}]}},
    }
    module._validate_container(
        actual,
        expected_id="a" * 64,
        name="fixed",
        image="image@sha256:fixed",
        container_port="3306/tcp",
        host_port=3317,
    )
    public = dict(actual)
    public["NetworkSettings"] = {"Ports": {"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3317"}]}}
    with pytest.raises(RuntimeError, match="identity changed"):
        module._validate_container(
            public,
            expected_id="a" * 64,
            name="fixed",
            image="image@sha256:fixed",
            container_port="3306/tcp",
            host_port=3317,
        )


def test_rabbit_id_drift_requires_matching_immutable_resolution(tmp_path, monkeypatch) -> None:
    module = _module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    resolution = tmp_path / "resolution.json"
    dependencies = {"containers": {"rabbitmq": {"id": "old"}}}
    actual = {"Id": "current"}
    monkeypatch.setattr(module, "DEPENDENCY_MANIFEST", manifest)
    monkeypatch.setattr(module, "DEPENDENCY_RESOLUTION", resolution)
    with pytest.raises(RuntimeError, match="without an immutable"):
        module._rabbit_current_id(dependencies, actual)
    resolution.write_text(
        json.dumps({
            "manifest_sha256": module._hash(manifest),
            "recorded_container_id": "old",
            "current_container_id": "current",
            "cause": "UNKNOWN",
            "identity_and_idle_checks_passed": True,
        }),
        encoding="utf-8",
    )
    assert module._rabbit_current_id(dependencies, actual) == "current"


def test_api_can_disable_enqueue_without_accepting_rabbit_resolution() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "ops" / "serve_auth001_retry.py").read_text(encoding="utf-8")
    assert "validate_runtime_assets(include_rabbit=enable_enqueue)" in source
    assert 'enable_enqueue=args.component != "api"' in launcher
    assert '"rabbitmq_boundary": "BLOCKED_CONTAINER_ID_UNKNOWN_NOT_ACCEPTED"' in source
