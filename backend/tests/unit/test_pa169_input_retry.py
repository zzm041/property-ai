"""PA-169: a failed input hash is quarantined only when the model answered.

The adapter used to quarantine every failed hash, so one runtime hiccup turned a
resident's exact wording into a permanent 503 that still said BUDGET_REJECTED.
These tests pin the three-way contract: runtime failures stay retryable, model
output failures stay quarantined, and an operator retry is attributed.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from app.provider.errors import (
    ProviderInputQuarantinedError,
    ProviderOutputUnverifiableError,
    ProviderUnavailableError,
)
from app.provider.http import request_json
from app.provider.llm import QwenLLMProvider


ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "local_ai_runtime_control_pa169",
    ROOT / "backend/app/provider/local_ai/runtime_control.py",
)
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)

INPUT_HASH = "b" * 64


def _runner(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "private": str(tmp_path / "run"),
                "host": "127.0.0.1",
                "port": 11436,
                # Only the behaviour test that drives run() needs these; the child
                # process is stubbed there, so the paths are never executed.
                "bge_python": "python",
                "asr_python": "python",
            }
        ),
        encoding="utf-8",
    )
    return m.Runner(path)


def _historical_failure(runner, *, reason, hash_value=INPUT_HASH, name="call-001"):
    folder = runner.private / name
    folder.mkdir(parents=True)
    m.save(folder / "reservation.json", {"kind": "qwen", "unix": 1.0, "input_hash": hash_value})
    result = {"status": "FAILED", "owned_exit_confirmed": True, "total_seconds": 1.0}
    if reason is not None:
        result["reason"] = reason
    m.save(folder / "result.json", result)
    return folder


def test_a_runtime_failure_does_not_quarantine_the_input(tmp_path):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason="RESOURCE_FLOOR")

    folder, _deadline = runner.reserve("qwen", INPUT_HASH)

    assert folder.name == "call-002"
    record = json.loads((folder / "reservation.json").read_text(encoding="utf-8"))
    assert record["replay_of"] is None
    assert record["retried_failures"] == [
        {"record": "call-001", "reason": "RESOURCE_FLOOR", "origin": "runtime"}
    ]
    assert "forced_retry" not in record


@pytest.mark.parametrize(
    "reason",
    [None, "RESOURCE_FLOOR", "RESOURCE_OBSERVATION_FAILED", "REMOTE_UNAVAILABLE", "INFERENCE_TIMEOUT"],
)
def test_every_runtime_reason_stays_retryable(tmp_path, reason):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason=reason)

    folder, _deadline = runner.reserve("qwen", INPUT_HASH)

    assert json.loads((folder / "reservation.json").read_text(encoding="utf-8"))["retried_failures"]


@pytest.mark.parametrize(
    "reason",
    ["MODEL_OUTPUT_REJECTED", "MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_SCHEMA_INVALID", "REMOTE_RESPONSE_INVALID"],
)
def test_a_model_output_failure_still_quarantines_the_input(tmp_path, reason):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason=reason)

    with pytest.raises(RuntimeError, match="FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY"):
        runner.reserve("qwen", INPUT_HASH)

    assert not (runner.private / "call-002").exists()


def test_force_retry_replays_a_quarantined_input_and_records_the_actor(tmp_path):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason="MODEL_OUTPUT_REJECTED")

    folder, _deadline = runner.reserve(
        "qwen", INPUT_HASH, force_retry={"actor": "user:9", "request_binding": "sha-binding"}
    )

    record = json.loads((folder / "reservation.json").read_text(encoding="utf-8"))
    assert folder.name == "call-002"
    assert record["forced_retry"]["actor"] == "user:9"
    assert record["forced_retry"]["request_binding"] == "sha-binding"
    assert record["forced_retry"]["quarantined"] == ["call-001"]
    assert record["forced_retry"]["utc"].endswith("+00:00")


def test_failure_origin_only_treats_model_output_answers_as_quarantine():
    for reason in (
        "MODEL_OUTPUT_REJECTED",
        "MODEL_OUTPUT_INVALID",
        "MODEL_OUTPUT_SCHEMA_INVALID",
        # PA-169/F2: the child ran but produced no single result line, which is
        # an unusable answer, not a busy machine.
        "REMOTE_RESPONSE_INVALID",
    ):
        assert m.failure_origin(reason) == "model_output"
    for reason in (
        None,
        "",
        "RESOURCE_FLOOR",
        "TIMEOUT",
        "MODEL_REQUEST_FAILED_NO_RETRY",
        "CPU_START_RAM_GUARD",
        "REMOTE_UNAVAILABLE",
        "REMOTE_CLIENT_FAILED",
    ):
        assert m.failure_origin(reason) == "runtime"


def test_the_result_record_carries_the_failure_origin(tmp_path, monkeypatch):
    """PA-169/F5: read the field a failure actually writes, not the source text."""

    runner = _runner(tmp_path)
    runner.require_runtime_binding = False
    monkeypatch.setattr(m, "qwen_guard", lambda: None)
    monkeypatch.setattr(m, "runtime_stop_required", lambda reason: False)
    monkeypatch.setattr(m, "RUNTIME_LEFT_RUNNING_REASONS", ())

    class _Child:
        pid = 4242
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    class _Job:
        name = "pa169-unit-job"

        def active(self):
            return False

        def terminate_and_confirm(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **k: _Child())
    monkeypatch.setattr(m, "RequestJob", _Job)

    body = {
        "model": m.MODELS["qwen"],
        "messages": [{"role": "user", "content": "synthetic"}],
        "response_format": m.WORK_ORDER_RESPONSE_FORMAT,
        "temperature": 0,
        "reasoning_effort": "none",
        "max_tokens": 512,
    }
    with pytest.raises(RuntimeError, match="REMOTE_RESPONSE_INVALID"):
        runner.run("qwen", body)

    result = json.loads((runner.private / "call-001" / "result.json").read_text(encoding="utf-8"))
    # With the runtime-stop step stubbed out the completion state stays unknown
    # (the real adapter stops the bound runtime and records FAILED); either way
    # the ledger has to record which side of the fence the failure came from.
    assert result["status"] in {"FAILED", "UNKNOWN"}
    assert result["reason"] == "REMOTE_RESPONSE_INVALID"
    assert result["failure_origin"] == "model_output"


def test_a_successful_record_carries_no_failure_origin(tmp_path):
    runner = _runner(tmp_path)
    folder = runner.private / "call-001"
    folder.mkdir(parents=True)
    m.save(folder / "reservation.json", {"kind": "qwen", "unix": 1.0, "input_hash": INPUT_HASH})
    m.save(folder / "result.json", {"status": "SUCCESS", "failure_origin": None, "total_seconds": 1.0})
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert result["failure_origin"] is None


def test_quarantine_is_reported_as_input_quarantined_not_budget():
    exc = RuntimeError("FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY")

    assert m.Runner._failure_code(exc) == "INPUT_QUARANTINED"
    assert m.failure_body(exc) == {
        "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY",
        "reason_code": "INPUT_QUARANTINED",
    }
    assert m.Runner._failure_code(RuntimeError("R2_CALL_LIMIT")) == "BUDGET_REJECTED"
    # PA-169/F3: the body names the reason for every refusal, not just this one.
    assert m.failure_body(RuntimeError("R2_CALL_LIMIT")) == {
        "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY",
        "reason_code": "BUDGET_REJECTED",
    }


def test_unknown_previous_completion_is_not_reported_as_a_spent_budget():
    exc = RuntimeError("PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY")

    assert m.Runner._failure_code(exc) == "PREVIOUS_REQUEST_UNKNOWN"
    assert m.failure_body(exc)["reason_code"] == "PREVIOUS_REQUEST_UNKNOWN"


def test_an_unverifiable_answer_gets_its_own_code_not_an_internal_error():
    exc = RuntimeError("REMOTE_RESPONSE_INVALID")

    assert m.Runner._failure_code(exc) == "OUTPUT_UNVERIFIABLE"
    assert m.failure_body(exc)["reason_code"] == "OUTPUT_UNVERIFIABLE"


def test_a_resource_refusal_carries_the_floor_numbers_to_the_caller():
    exc = RuntimeError("RESOURCE_FLOOR")
    exc.resource_floor = {
        "ok": False,
        "short_by_mib": 38,
        "floor_mib": 2000,
        "observed_mib": 1962,
        "ram": {"ok": True},
        "gpu": {"short_by_mib": 38},
    }

    body = m.failure_body(exc)
    assert body["reason_code"] == "RESOURCE_ADMISSION_REJECTED"
    assert body["resource_floor"]["short_by_mib"] == 38


def test_provider_maps_the_quarantine_body_to_its_own_error():
    def handler(request):
        return httpx.Response(503, json={"error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY",
                                         "reason_code": "INPUT_QUARANTINED"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderInputQuarantinedError) as caught:
        request_json(method="POST", url="http://127.0.0.1:11437/v1/chat/completions",
                     timeout_seconds=1.0, json_body={"model": "synthetic"}, client=client,
                     provider="llm", operation="structured_completion")
    assert caught.value.error_code == "AI.INPUT_QUARANTINED"


def test_provider_maps_an_unverifiable_answer_to_its_own_error():
    def handler(request):
        return httpx.Response(
            503,
            json={"error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY", "reason_code": "OUTPUT_UNVERIFIABLE"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderOutputUnverifiableError) as caught:
        request_json(method="POST", url="http://127.0.0.1:11437/v1/chat/completions",
                     timeout_seconds=1.0, json_body={"model": "synthetic"}, client=client,
                     provider="llm", operation="structured_completion")
    assert caught.value.error_code == "AI.OUTPUT_UNVERIFIABLE"


def test_provider_keeps_the_floor_numbers_of_a_resource_refusal():
    def handler(request):
        return httpx.Response(
            503,
            json={
                "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY",
                "reason_code": "RESOURCE_ADMISSION_REJECTED",
                "resource_floor": {"ok": False, "short_by_mib": 38, "floor_mib": 2000, "observed_mib": 1962},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailableError) as caught:
        request_json(method="POST", url="http://127.0.0.1:11437/v1/chat/completions",
                     timeout_seconds=1.0, json_body={"model": "synthetic"}, client=client,
                     provider="llm", operation="structured_completion")
    assert caught.value.resource_floor["short_by_mib"] == 38


def test_provider_keeps_a_plain_503_as_unavailable():
    def handler(request):
        return httpx.Response(503, json={"error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailableError):
        request_json(method="POST", url="http://127.0.0.1:11437/v1/chat/completions",
                     timeout_seconds=1.0, json_body={"model": "synthetic"}, client=client,
                     provider="llm", operation="structured_completion")


def test_the_operator_retry_travels_in_a_header_of_its_own():
    seen = {}

    def handler(request):
        seen["actor"] = request.headers.get("X-Force-Retry-Actor")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    provider = QwenLLMProvider(
        base_url="http://127.0.0.1:11437/v1",
        model="synthetic",
        api_key="synthetic-key",
        timeout_seconds=1.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider.structured_completion(prompt="synthetic", prompt_version="rag-answer-v2")
    assert seen["actor"] is None

    provider.structured_completion(prompt="synthetic", prompt_version="rag-answer-v2",
                                   force_retry_actor="user:9")
    assert seen["actor"] == "user:9"
