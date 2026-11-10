"""PA-130: the adapter's resource floor is observable and configurable.

The floor used to be three literals inside the adapter. A host that missed the
floor by a few dozen MiB could only report "rejected", and changing the value
meant editing the reviewed file. These tests pin the contract that replaced it:
the three defaults do not move, the environment can override them, a malformed
override stops the process at import, and every refusal can state which
dimension is short and by how many MiB.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load():
    spec = importlib.util.spec_from_file_location(
        "local_ai_http_pa130", ROOT / "scripts/ops/local_ai_http.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def m():
    return load()


def reload_with(m, monkeypatch, **env):
    """Execute the adapter again, so import-time floor resolution runs anew."""
    for name in ("LOCAL_AI_RAM_FLOOR_MIB", "LOCAL_AI_GPU_FLOOR_MIB", "LOCAL_AI_COLD_START_GPU_FLOOR_MIB"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load()


def test_the_shipped_floors_lose_nothing(m, monkeypatch):
    fresh = reload_with(m, monkeypatch)
    assert (fresh.QWEN_RUNNING_RAM_FLOOR_MIB, fresh.QWEN_RUNNING_GPU_FLOOR_MIB,
            fresh.QWEN_COLD_START_GPU_FLOOR_MIB) == (4500, 2000, 6000)
    floors = fresh.effective_floors()
    assert [floors[key]["source"] for key in ("ram", "gpu", "cold_start_gpu")] == ["default"] * 3
    assert floors["defaults"] == {"ram": 4500, "gpu": 2000, "cold_start_gpu": 6000}


def test_environment_overrides_are_effective_and_named(m, monkeypatch):
    fresh = reload_with(
        m, monkeypatch,
        LOCAL_AI_RAM_FLOOR_MIB="5200",
        LOCAL_AI_GPU_FLOOR_MIB="2600",
        LOCAL_AI_COLD_START_GPU_FLOOR_MIB="7100",
    )
    assert (fresh.QWEN_RUNNING_RAM_FLOOR_MIB, fresh.QWEN_RUNNING_GPU_FLOOR_MIB,
            fresh.QWEN_COLD_START_GPU_FLOOR_MIB) == (5200, 2600, 7100)
    floors = fresh.effective_floors()
    assert floors["ram"] == {"floor_mib": 5200, "source": "env:LOCAL_AI_RAM_FLOOR_MIB"}
    assert floors["gpu"] == {"floor_mib": 2600, "source": "env:LOCAL_AI_GPU_FLOOR_MIB"}
    assert floors["cold_start_gpu"] == {"floor_mib": 7100, "source": "env:LOCAL_AI_COLD_START_GPU_FLOOR_MIB"}


@pytest.mark.parametrize("value", ["abc", "0", "-5", "12.5", "262145", "  "])
def test_an_unusable_override_stops_the_adapter(m, monkeypatch, value):
    for name in ("LOCAL_AI_RAM_FLOOR_MIB", "LOCAL_AI_GPU_FLOOR_MIB", "LOCAL_AI_COLD_START_GPU_FLOOR_MIB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCAL_AI_RAM_FLOOR_MIB", value)
    if not value.strip():
        # A blank value means "not set": the default applies rather than a guess.
        assert load().QWEN_RUNNING_RAM_FLOOR_MIB == 4500
        return
    with pytest.raises(ValueError, match="INVALID_FLOOR_MIB:LOCAL_AI_RAM_FLOOR_MIB"):
        load()


def test_short_by_mib_is_positive_exactly_when_below_the_floor(m):
    short = m.resource_floor(ram_mib=4441, gpu_mib=1941)
    assert short["ok"] is False
    assert short["ram"]["short_by_mib"] == 59 and short["gpu"]["short_by_mib"] == 59
    assert short["short_by_mib"] == 59
    # The reported pair names the dimension that is short, not just the code.
    assert (short["floor_mib"], short["observed_mib"]) == (4500, 4441)

    exact = m.resource_floor(ram_mib=4500, gpu_mib=2000)
    assert exact["ok"] is True and exact["short_by_mib"] == 0
    roomy = m.resource_floor(ram_mib=9000, gpu_mib=9000)
    assert roomy["ok"] is True and roomy["short_by_mib"] == 0


def test_the_cold_start_floor_applies_while_the_model_is_not_resident(m):
    cold = m.resource_floor(ram_mib=9000, gpu_mib=5000, cold_start=True)
    assert cold["gpu"]["floor_mib"] == 6000 and cold["gpu"]["short_by_mib"] == 1000
    assert cold["ok"] is False and cold["cold_start"] is True
    running = m.resource_floor(ram_mib=9000, gpu_mib=5000)
    assert running["ok"] is True and running["gpu"]["floor_mib"] == 2000


def test_a_non_gpu_kind_does_not_inherit_the_gpu_floor(m):
    snapshot = m.resource_floor(ram_mib=6000, skip_gpu=True)
    assert snapshot["ok"] is True
    assert snapshot["gpu"]["skipped"] is True and snapshot["gpu"]["short_by_mib"] is None


def test_an_unreadable_measurement_is_not_an_admission(m, monkeypatch):
    def boom():
        raise RuntimeError("GPU_CHECK_FAILED")

    monkeypatch.setattr(m, "gpu", boom)
    snapshot = m.resource_floor(ram_mib=9000)
    assert snapshot["ok"] is False
    assert snapshot["gpu"]["observed_mib"] is None
    assert snapshot["gpu"]["observation_error"] == "RuntimeError"
    assert snapshot["gpu"]["short_by_mib"] is None


def test_the_guard_refusal_carries_the_numbers(m, monkeypatch):
    monkeypatch.setattr(m, "direct", lambda path, body=None, timeout=3: {
        "/api/version": {"version": m.QWEN_RUNTIME_VERSION},
        "/api/tags": {"models": [{"name": m.QWEN_RUNTIME_MODEL, "digest": m.DIGEST}]},
        "/api/ps": {"models": []},
    }[path])
    monkeypatch.setattr(m, "ram", lambda: 4441)
    monkeypatch.setattr(m, "gpu", lambda: 1941)
    with pytest.raises(m.ResourceFloorRefused) as caught:
        m.qwen_guard()
    assert str(caught.value) == "QWEN_RESOURCE_GUARD"
    snapshot = caught.value.resource_floor
    # Nothing is resident, so the cold-start GPU floor (6000) is the one that
    # applies; with 1941 MiB free the GPU is 4059 MiB short and the RAM 59 MiB.
    assert snapshot["cold_start"] is True
    assert snapshot["ram"]["short_by_mib"] == 59
    assert snapshot["gpu"]["floor_mib"] == 6000 and snapshot["gpu"]["short_by_mib"] == 4059
    assert snapshot["short_by_mib"] == 4059
    assert m.Runner._failure_code(caught.value) == "RESOURCE_ADMISSION_REJECTED"


def test_the_guard_admits_once_the_floor_is_overridden(m, monkeypatch):
    monkeypatch.setattr(m, "direct", lambda path, body=None, timeout=3: {
        "/api/version": {"version": m.QWEN_RUNTIME_VERSION},
        "/api/tags": {"models": [{"name": m.QWEN_RUNTIME_MODEL, "digest": m.DIGEST}]},
        "/api/ps": {"models": []},
    }[path])
    monkeypatch.setattr(m, "ram", lambda: 4441)
    monkeypatch.setattr(m, "gpu", lambda: 1941)
    m.QWEN_RUNNING_RAM_FLOOR_MIB = 4400
    m.QWEN_COLD_START_GPU_FLOOR_MIB = 1900
    m.QWEN_RUNNING_GPU_FLOOR_MIB = 1900
    assert m.qwen_guard() is None


def test_the_503_body_names_the_shortfall_without_changing_the_error(m, monkeypatch):
    monkeypatch.setattr(m, "direct", lambda path, body=None, timeout=3: {
        "/api/version": {"version": m.QWEN_RUNTIME_VERSION},
        "/api/tags": {"models": [{"name": m.QWEN_RUNTIME_MODEL, "digest": m.DIGEST}]},
        "/api/ps": {"models": [{"name": m.QWEN_RUNTIME_MODEL, "digest": m.DIGEST,
                                "context_length": m.QWEN_CONTEXT_LENGTH}]},
    }[path])
    monkeypatch.setattr(m, "ram", lambda: 4462)
    monkeypatch.setattr(m, "gpu", lambda: 1962)
    with pytest.raises(m.ResourceFloorRefused) as caught:
        m.qwen_guard()
    body = m.failure_body(caught.value)
    assert body["error"] == "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY"
    assert body["resource_floor"]["ram"]["short_by_mib"] == 38
    assert body["resource_floor"]["gpu"]["short_by_mib"] == 38
    assert body["resource_floor"]["short_by_mib"] == 38
    # Any other refusal keeps exactly the old body.
    assert m.failure_body(RuntimeError("MODEL_REQUEST_FAILED_NO_RETRY")) == {
        "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY"
    }


def test_the_failure_record_keeps_the_code_and_adds_the_floor(tmp_path, m):
    runner = m.Runner.__new__(m.Runner)
    runner.private = tmp_path
    runner.scope = "work_order_only"
    import threading

    runner.audit_lock = threading.Lock()
    refused = m.ResourceFloorRefused({
        "ok": False, "short_by_mib": 38, "cold_start": True,
        "ram": {"floor_mib": 4500, "observed_mib": 4462, "short_by_mib": 38, "ok": False},
        "gpu": {"floor_mib": 2000, "observed_mib": 9000, "short_by_mib": 0, "ok": True},
    })
    reason = runner.record_pre_reservation_failure(
        refused, stage="MODEL_RESIDENCY_AND_RESOURCE", kind="qwen",
        input_hash="a" * 64, request_binding="b" * 64,
    )
    record = json.loads((tmp_path / "pre-reservation-failures.jsonl").read_text(encoding="utf-8"))
    assert reason == "RESOURCE_ADMISSION_REJECTED"
    # detail_code keeps its original meaning: only whitelisted, non-predictive
    # strings are echoed, and QWEN_RESOURCE_GUARD is not one of them.
    assert record["detail_code"] is None
    assert record["resource_floor"]["short_by_mib"] == 38
    assert record["resource_floor"]["ram"]["observed_mib"] == 4462
    assert record["reservation_created"] is False

    runner.record_pre_reservation_failure(
        ValueError("PCM_REQUIRED"), stage="HTTP_DECODE", kind="asr"
    )
    second = json.loads((tmp_path / "pre-reservation-failures.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert second["resource_floor"] is None
