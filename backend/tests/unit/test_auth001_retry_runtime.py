from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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
    assert module.SLA_ACCEPTANCE_QUEUE == "property_ai.auth001.persist.v2.sla.acceptance"
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

    resolution.write_text(
        json.dumps({
            "manifest_sha256": module._hash(manifest),
            "recorded_container_id": "old",
            "current_container_id": "current",
            "cause": "UNKNOWN",
            "identity_checks_passed": True,
            "existing_messages_preserved": True,
            "existing_queue_consumption_forbidden": True,
        }),
        encoding="utf-8",
    )
    assert module._rabbit_current_id(dependencies, actual) == "current"


def test_api_can_disable_ai_enqueue_but_still_requires_rabbit_resolution() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "ops" / "serve_auth001_retry.py").read_text(encoding="utf-8")
    assert "validate_runtime_assets(include_rabbit=require_rabbit_identity)" in source
    assert 'enable_enqueue=args.component != "api"' in launcher
    assert "require_rabbit_identity=True" in launcher
    assert '"rabbitmq_boundary": "BLOCKED_CONTAINER_ID_UNKNOWN_NOT_ACCEPTED"' in source


def test_identity_test_clock_is_exactly_scoped_and_timezone_aware() -> None:
    module = _module()
    exact = SimpleNamespace(
        app_env="test",
        identity_test_proof_enabled=True,
        identity_test_proof_target="127.0.0.1:3317/property_ai_auth001_test",
        active_database_url=(
            "mysql+pymysql://fixture:fixture@127.0.0.1:3317/"
            "property_ai_auth001_test"
        ),
    )
    value = module.parse_identity_test_clock(exact, "2026-08-28T04:00:00+08:00")
    assert value.isoformat() == "2026-08-27T20:00:00+00:00"
    assert module.parse_identity_test_clock(exact, None) is None
    with pytest.raises(RuntimeError, match="explicit timezone"):
        module.parse_identity_test_clock(exact, "2026-08-28T04:00:00")

    wrong_port = SimpleNamespace(**{**vars(exact), "active_database_url": (
        "mysql+pymysql://fixture:fixture@127.0.0.1:3307/"
        "property_ai_auth001_test"
    )})
    with pytest.raises(RuntimeError, match="exact isolated 3317"):
        module.parse_identity_test_clock(wrong_port, "2026-08-28T04:00:00Z")


def test_api_clock_override_is_host_side_and_dependency_scoped() -> None:
    launcher = (ROOT / "scripts" / "ops" / "serve_auth001_retry.py").read_text(encoding="utf-8")
    assert 'os.environ.get("AUTH001_IDENTITY_TEST_CLOCK_UTC")' in launcher
    assert "dependency_overrides[get_identity_recovery_service]" in launcher
    assert "IsolatedTestIdentityProofProvider" in launcher


def test_bounded_beat_requires_exact_ids_and_uses_the_isolated_acceptance_queue() -> None:
    launcher = (ROOT / "scripts" / "ops" / "serve_auth001_retry.py").read_text(
        encoding="utf-8"
    )
    assert 'choices=("api", "worker", "beat", "publisher")' in launcher
    assert 'parser.error("beat requires an exact work-order ID file")' in launcher
    assert "not 1 <= len(ids) <= 4" in launcher
    assert '"task": "app.worker.tasks.sweep_sla_task"' in launcher
    assert '"args": (work_order_id,)' in launcher
    assert "queue_name=SLA_ACCEPTANCE_QUEUE" in launcher
    assert "else QUEUE" not in launcher
    assert '"worker", "-Q", settings.resolved_celery_queue' in launcher
    assert '"options": {"queue": settings.resolved_celery_queue}' in launcher


def test_acceptance_api_routes_notifications_away_from_the_protected_queue() -> None:
    launcher = (ROOT / "scripts" / "ops" / "serve_auth001_retry.py").read_text(
        encoding="utf-8"
    )
    assert 'enable_enqueue=args.component != "api"' in launcher
    assert "queue_name=SLA_ACCEPTANCE_QUEUE" in launcher
    assert "The API may enqueue notification-delivery work" in launcher


def test_retry_runtime_queue_allowlist_is_closed(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "validate_runtime_assets", lambda **_kwargs: {})
    with pytest.raises(RuntimeError, match="fixed allowlist"):
        module.build_settings(queue_name="property_ai.auth001.persist.v2.unapproved")
