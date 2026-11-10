"""PA-132: releasing a shared completion barrier is a tool with a receipt, not a `del`.

The gate stays fail-closed: while the barrier file exists every request is blocked. What
changes is the way out - the barrier now describes itself, and `ai_barrier.resolve` archives
it with an operator decision instead of destroying the evidence.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolve their own module entry while decorating, so register it first
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load("local_ai_http_132", "backend/app/provider/local_ai/runtime_control.py")
barrier_tool = _load("ai_barrier_132", "scripts/ops/ai_barrier.py")


def _runner(tmp_path: Path):
    private = tmp_path / "shared" / "calls"
    private.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"private": str(private), "host": "127.0.0.1", "port": 11436}), encoding="utf-8")
    runner = adapter.Runner(config)
    runner.shared_lock_path = tmp_path / "shared" / "model.lock"
    runner.shared_completion_barrier_path = tmp_path / "shared" / "completion-unknown.json"
    return runner


UNKNOWN_RESULT = {
    "status": "UNKNOWN",
    "reason": "RESOURCE_FLOOR",
    "owned_exit_confirmed": False,
    "remote_completion": "UNKNOWN",
}


def _make_barrier(tmp_path: Path):
    runner = _runner(tmp_path)
    folder = runner.private / "call-051"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(UNKNOWN_RESULT), encoding="utf-8")
    handle = runner.acquire_shared_model_lock()
    try:
        runner.record_shared_completion_unknown(folder, UNKNOWN_RESULT)
    finally:
        runner.release_shared_model_lock(handle)
    return runner, folder


def test_barrier_describes_itself_so_release_needs_no_source_reading(tmp_path: Path) -> None:
    runner, _folder = _make_barrier(tmp_path)
    payload = json.loads((tmp_path / "shared" / "completion-unknown.json").read_text(encoding="utf-8"))
    assert payload["format"] == "property-ai-shared-model-completion-barrier-v1"
    assert payload["call_id"] == "call-051"
    assert payload["record"] == "call-051"
    assert payload["gate"] == "BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY"
    # the barrier is shared by every scope on this host, so a release decision must name all
    # of them - including the scope that produced it and the RAG chain that did not
    assert set(payload["affected_scopes"]) >= {runner.scope, "rag"}
    remediation = payload["remediation"]
    assert "resolve-unknown -Call call-051" in remediation["command"]
    assert remediation["requires_restart"] is False
    assert remediation["replay_authorized"] is False


def test_gate_stays_fail_closed_until_the_barrier_is_released(tmp_path: Path) -> None:
    runner, _folder = _make_barrier(tmp_path)
    assert runner.gate() == "BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY"

    info = barrier_tool.describe(barrier_tool.read_barrier(runner.shared_completion_barrier_path))
    assert info["call_id"] == "call-051"
    assert info["reason"] == "RESOURCE_FLOOR"
    assert set(info["scopes"]) >= {runner.scope, "rag"}

    # the gate does not care that a description exists - only the file does
    assert runner.gate() == "BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY"

    receipt = barrier_tool.resolve(
        runner.shared_completion_barrier_path,
        call="call-051",
        reason="floor failure with no replay pending; runtime verified idle by hand",
        operator="property-dev",
    )
    assert receipt["format"] == "property-ai-completion-barrier-resolution-v1"
    assert receipt["call_id"] == "call-051"
    assert receipt["action"] == "BARRIER_ARCHIVED_AND_RELEASED"
    assert receipt["restart_required"] is False
    assert receipt["replay_authorized"] is False
    assert receipt["operator"] == "property-dev"
    assert receipt["barrier"]["sha256"]
    assert receipt["barrier"]["bytes_preserved"] > 0

    archive = Path(receipt["barrier"]["archive_path"])
    assert archive.is_file()
    archived = json.loads(archive.read_text(encoding="utf-8"))
    # nothing is thrown away: the original record survives with the decision appended
    for key, value in {"status": "UNKNOWN", "reason": "RESOURCE_FLOOR", "record": "call-051"}.items():
        assert archived[key] == value
    assert archived["resolution"]["operator"] == "property-dev"
    assert archived["resolution"]["original_sha256"] == receipt["barrier"]["sha256"]

    assert not runner.shared_completion_barrier_path.exists()
    assert runner.gate() == "READY"


def test_release_requires_the_matching_call_and_a_reason(tmp_path: Path) -> None:
    runner, _folder = _make_barrier(tmp_path)
    with pytest.raises(barrier_tool.BarrierError, match="not 'call-999'"):
        barrier_tool.resolve(runner.shared_completion_barrier_path, call="call-999", reason="why not")
    assert runner.shared_completion_barrier_path.exists(), "a refused release must leave the chain blocked"

    with pytest.raises(barrier_tool.BarrierError, match="reason is required"):
        barrier_tool.resolve(runner.shared_completion_barrier_path, call="call-051", reason="   ")
    assert runner.shared_completion_barrier_path.exists()


def test_unreadable_or_foreign_barriers_are_refused(tmp_path: Path) -> None:
    missing = tmp_path / "completion-unknown.json"
    with pytest.raises(barrier_tool.BarrierError, match="no completion barrier"):
        barrier_tool.read_barrier(missing)

    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    with pytest.raises(barrier_tool.BarrierError, match="unreadable"):
        barrier_tool.read_barrier(garbage)

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"format": "something-else", "record": "call-001"}), encoding="utf-8")
    with pytest.raises(barrier_tool.BarrierError, match="unexpected barrier format"):
        barrier_tool.read_barrier(foreign)


def test_cli_reports_success_and_failure_with_distinct_exit_codes(tmp_path: Path, capsys) -> None:
    runner, _folder = _make_barrier(tmp_path)
    barrier = runner.shared_completion_barrier_path

    assert barrier_tool.main(["--barrier", str(barrier), "resolve", "--call", "call-002", "--reason", "x"]) == 2
    assert "not 'call-002'" in capsys.readouterr().out

    assert barrier_tool.main(["--barrier", str(barrier), "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["call_id"] == "call-051"

    assert (
        barrier_tool.main(
            ["--barrier", str(barrier), "resolve", "--call", "call-051", "--reason", "no replay pending", "--operator", "ops"]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["operator"] == "ops"

    assert barrier_tool.main(["--barrier", str(barrier), "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert runner.gate() == "READY"


def test_adapter_barrier_fields_are_additive_only() -> None:
    source = (ROOT / "backend/app/provider/local_ai/runtime_control.py").read_text(encoding="utf-8")
    # the gate decision must remain "does the file exist", not a parsed field
    assert "if self.shared_completion_barrier_path.exists():\n            return 'BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY'" in source
    for field in ("'call_id':folder.name", "'affected_scopes':", "'remediation':{"):
        assert field in source
