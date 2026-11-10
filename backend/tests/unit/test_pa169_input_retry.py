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
        json.dumps({"private": str(tmp_path / "run"), "host": "127.0.0.1", "port": 11436}),
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


@pytest.mark.parametrize("reason", [None, "RESOURCE_FLOOR", "RESOURCE_OBSERVATION_FAILED", "REMOTE_RESPONSE_INVALID"])
def test_every_runtime_reason_stays_retryable(tmp_path, reason):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason=reason)

    folder, _deadline = runner.reserve("qwen", INPUT_HASH)

    assert json.loads((folder / "reservation.json").read_text(encoding="utf-8"))["retried_failures"]


def test_a_model_output_failure_still_quarantines_the_input(tmp_path):
    runner = _runner(tmp_path)
    _historical_failure(runner, reason="MODEL_OUTPUT_REJECTED")

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
    for reason in ("MODEL_OUTPUT_REJECTED", "MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_SCHEMA_INVALID"):
        assert m.failure_origin(reason) == "model_output"
    for reason in (None, "", "RESOURCE_FLOOR", "TIMEOUT", "MODEL_REQUEST_FAILED_NO_RETRY", "CPU_START_RAM_GUARD"):
        assert m.failure_origin(reason) == "runtime"


def test_the_result_record_carries_the_failure_origin():
    source = (ROOT / "backend/app/provider/local_ai/runtime_control.py").read_text(encoding="utf-8")
    assert "'failure_origin':None if success else failure_origin(reason)" in source


def test_quarantine_is_reported_as_input_quarantined_not_budget():
    exc = RuntimeError("FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY")

    assert m.Runner._failure_code(exc) == "INPUT_QUARANTINED"
    assert m.failure_body(exc) == {
        "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY",
        "reason_code": "INPUT_QUARANTINED",
    }
    assert m.Runner._failure_code(RuntimeError("R2_CALL_LIMIT")) == "BUDGET_REJECTED"
    assert m.failure_body(RuntimeError("R2_CALL_LIMIT")) == {
        "error": "MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY"
    }


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
