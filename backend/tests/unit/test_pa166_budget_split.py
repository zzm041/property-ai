"""PA-166: the cumulative model budget and the reviewed evidence must not share one counter.

Before this, every ``call-*`` folder under the private run directory counted as
spend. Restoring the reviewed assets (``deploy/ai-runtime.ps1 up`` refuses to
start when ``call-001`` is missing) therefore spent the very budget that the
restore was supposed to prove, and the failure record could not tell the two
apart. These tests pin the split in both directions: evidence costs nothing,
real calls still cost.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("local_ai_http_pa166", ROOT / "scripts/ops/local_ai_http.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

QWEN_LIMITS = {"asr": 6, "embedding": 12, "reranker": 8, "qwen": 2}


def _config(tmp_path, **overrides):
    config = {
        "private": str(tmp_path / "run"),
        "host": "127.0.0.1",
        "port": 11436,
        "limits": dict(QWEN_LIMITS),
        "call_baseline": {"asr": 0, "embedding": 0, "reranker": 0, "qwen": 0},
        "duration_seconds": 7200,
        "duration_baseline_seconds": 0,
    }
    config.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _call_folder(private, index, *, kind="qwen", status="SUCCESS", elapsed=1.0):
    folder = Path(private) / f"call-{index:03d}"
    folder.mkdir(parents=True)
    reservation = {
        "kind": kind, "unix": 1.0, "utc": "2026-11-11T00:00:00+00:00",
        "model": m.MODELS[kind], "max_calls": 105, "input_hash": f"history-{index}",
    }
    (folder / "reservation.json").write_text(json.dumps(reservation), encoding="utf-8")
    result = {"status": status, "total_seconds": elapsed}
    (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return folder


def _history(private, count, **kwargs):
    return [_call_folder(private, index + 1, **kwargs) for index in range(count)]


def test_historical_evidence_does_not_consume_the_window(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    folders = _history(private, 40, elapsed=900.0)
    names = [folder.name for folder in folders]
    runner = m.Runner(_config(tmp_path, evidence_records=names))
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)

    usage = runner.budget_usage()
    assert usage["evidence_excluded"] == 40
    assert usage["counted"] == {"asr": 0, "embedding": 0, "reranker": 0, "qwen": 0}
    assert usage["limits"]["qwen"] == 2

    first, deadline = runner.reserve("qwen", "first")
    assert first.name == "call-041"
    assert deadline == pytest.approx(8200.0)  # evidence seconds never charge the window
    m.save(first / "result.json", {"status": "SUCCESS", "total_seconds": 1.0})
    assert runner.reserve("qwen", "second")[0].name == "call-042"


def test_real_calls_still_deduct_from_the_same_window(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    names = [folder.name for folder in _history(private, 40)]
    runner = m.Runner(_config(tmp_path, evidence_records=names))
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)
    for index, value in enumerate(("one", "two"), start=41):
        folder, _ = runner.reserve("qwen", value)
        assert folder.name == f"call-{index:03d}"
        m.save(folder / "result.json", {"status": "SUCCESS", "total_seconds": 1.0})
    with pytest.raises(RuntimeError, match="R2_CALL_LIMIT"):
        runner.reserve("qwen", "three")
    assert runner.budget_usage()["counted"]["qwen"] == 2


def test_call_baseline_and_counted_calls_add_up(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    names = [folder.name for folder in _history(private, 10)]
    runner = m.Runner(
        _config(tmp_path, evidence_records=names,
                call_baseline={"asr": 0, "embedding": 0, "reranker": 0, "qwen": 1})
    )
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)
    folder, _ = runner.reserve("qwen", "only-one-left")
    assert folder.name == "call-011"
    m.save(folder / "result.json", {"status": "SUCCESS", "total_seconds": 1.0})
    with pytest.raises(RuntimeError, match="R2_CALL_LIMIT"):
        runner.reserve("qwen", "over")


def test_reviewed_resolution_records_are_evidence_too(tmp_path):
    runner = m.Runner(_config(tmp_path, resolutions=[{"record": "call-001"}]))
    assert "call-001" in runner.evidence_records


def test_gate_still_fails_closed_when_evidence_is_incomplete(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    folder = _call_folder(private, 1, status="FAILED")
    (folder / "result.json").unlink()
    runner = m.Runner(_config(tmp_path, evidence_records=["call-001"]))
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)
    with pytest.raises(RuntimeError, match="PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY"):
        runner.reserve("qwen", "after-broken-evidence")


@pytest.mark.parametrize("value", ["call-001", [1], [""], ["  "], {"call-001": True}])
def test_declared_evidence_must_be_a_list_of_names(tmp_path, value):
    with pytest.raises(ValueError, match="INVALID_EVIDENCE_RECORDS"):
        m.Runner(_config(tmp_path, evidence_records=value))


def test_new_reservations_never_reuse_an_evidence_index(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    _history(private, 3)
    runner = m.Runner(_config(tmp_path, evidence_records=["call-001", "call-002", "call-003"]))
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)
    folder, _ = runner.reserve("embedding", "fresh")
    assert folder.name == "call-004"


def test_reservation_records_its_budget_split(tmp_path, monkeypatch):
    private = tmp_path / "run"
    private.mkdir()
    names = [folder.name for folder in _history(private, 7)]
    runner = m.Runner(_config(tmp_path, evidence_records=names))
    monkeypatch.setattr(m.time, "time", lambda: 1000.0)
    folder, _ = runner.reserve("reranker", "split")
    record = json.loads((folder / "reservation.json").read_text(encoding="utf-8"))
    assert record["budget_counted"] is True
    assert record["evidence_excluded_before"] == 7
    assert record["call_baseline"] == 0
