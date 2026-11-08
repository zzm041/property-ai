"""A named-window budget counts failed attempts and rejects bypasses before IO."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("window_budget", Path(__file__).parents[1] / "window_budget.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


def request(**overrides):
    return {"method": "POST", "url": "http://127.0.0.1:11435/v1/chat/completions",
            "timeout_seconds": 90, "json_body": {"model": "fixture-model", "messages": []},
            "headers": {"Authorization": "do-not-record"}, **overrides}


def ledger(tmp_path, clock=lambda: 0, limits=None, maximum=2):
    return budget.WindowBudget(tmp_path / "ledger", model="fixture-model", limits=limits or {"text": 1, "agent": 2},
                               max_attempts=maximum, wall_seconds=30, clock=clock)


def test_text_and_agent_share_total_cap_and_do_not_record_headers(tmp_path):
    window = ledger(tmp_path)
    transport = lambda **kwargs: {"value": 42}
    assert window.invoke("text", transport, **request()) == {"value": 42}
    assert window.invoke("agent", transport, **request()) == {"value": 42}
    with pytest.raises(budget.WindowStopped):
        window.invoke("agent", lambda **kwargs: pytest.fail("Budget bypass"), **request())
    state = json.loads((tmp_path / "ledger/state.json").read_text())
    assert state["attempts_reserved"] == 2
    assert state["by_case"] == {"text": 1, "agent": 1}
    assert all("do-not-record" not in p.read_text() for p in (tmp_path / "ledger").glob("*.json"))


def test_per_case_limit_prevents_retry_even_with_global_room(tmp_path):
    window = ledger(tmp_path, maximum=3)
    window.invoke("text", lambda **kwargs: {}, **request())
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", lambda **kwargs: pytest.fail("Text retried"), **request())


def test_failed_attempt_is_reserved_and_stops_further_inference(tmp_path):
    window = ledger(tmp_path)
    def timeout(**kwargs):
        raise TimeoutError("synthetic timeout")
    with pytest.raises(TimeoutError):
        window.invoke("text", timeout, **request())
    with pytest.raises(budget.WindowStopped):
        window.invoke("agent", lambda **kwargs: pytest.fail("Retried after failure"), **request())
    state = json.loads((tmp_path / "ledger/state.json").read_text())
    assert state["attempts_reserved"] == 1 and state["stopped"]
    assert state["records"][0]["error_type"] == "TimeoutError"


@pytest.mark.parametrize("invalid", [
    {"url": "https://external.invalid/v1/chat/completions"},
    {"url": "http://127.0.0.1:11435/v1/other"},
    {"method": "GET"}, {"json_body": {"model": "other"}},
])
def test_wrong_target_or_model_is_rejected_without_call(tmp_path, invalid):
    window = ledger(tmp_path)
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", lambda **kwargs: pytest.fail("Unapproved target"), **request(**invalid))
    assert json.loads((tmp_path / "ledger/state.json").read_text())["attempts_reserved"] == 0


def test_deadline_limits_request_timeout_and_blocks_next_request(tmp_path):
    now = [10]
    window = ledger(tmp_path, clock=lambda: now[0])
    now[0] = 35
    def transport(**kwargs):
        assert kwargs["timeout_seconds"] == 5
        now[0] = 41
        return {}
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", transport, **request())
    with pytest.raises(budget.WindowStopped):
        window.invoke("agent", lambda **kwargs: pytest.fail("After deadline"), **request())


def test_unknown_case_and_existing_ledger_cannot_reset_budget(tmp_path):
    window = ledger(tmp_path)
    with pytest.raises(budget.WindowStopped):
        window.invoke("unknown", lambda **kwargs: pytest.fail("Unknown case"), **request())
    with pytest.raises(FileExistsError):
        ledger(tmp_path)


def test_failed_evidence_write_prevents_provider_call(tmp_path, monkeypatch):
    window = ledger(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(window, "_save", fail)
    with pytest.raises(OSError):
        window.invoke("text", lambda **kwargs: pytest.fail("No durable reservation"), **request())


def test_monitor_cancellation_during_transport_cannot_return_success(tmp_path):
    window = ledger(tmp_path)
    def transport(**kwargs):
        window.stop("RESOURCE_FLOOR")
        return {}
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", transport, **request())


@pytest.mark.parametrize("timeout", [None, 0, -1, float("nan")])
def test_invalid_timeout_never_reserves_or_starts_transport(tmp_path, timeout):
    window = ledger(tmp_path)
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", lambda **kwargs: pytest.fail("Invalid timeout"),
                      **request(timeout_seconds=timeout))
    assert json.loads((tmp_path / "ledger/state.json").read_text())["attempts_reserved"] == 0


def test_transient_windows_replace_failure_is_retried_only_for_evidence(tmp_path, monkeypatch):
    window = ledger(tmp_path)
    original = budget.os.replace
    failures = [2]
    def replace(source, destination):
        if failures[0]:
            failures[0] -= 1
            raise PermissionError("transient owned-file lock")
        original(source, destination)
    monkeypatch.setattr(budget.os, "replace", replace)
    assert window.invoke("text", lambda **kwargs: {"once": True}, **request()) == {"once": True}
    assert json.loads((tmp_path / "ledger/state.json").read_text())["attempts_reserved"] == 1


def test_persistent_replace_failure_still_prevents_inference(tmp_path, monkeypatch):
    window = ledger(tmp_path)
    replacements = []
    def replace(*args):
        replacements.append(args)
        raise PermissionError("persistent denied write")
    monkeypatch.setattr(budget.os, "replace", replace)
    with pytest.raises(PermissionError):
        window.invoke("text", lambda **kwargs: pytest.fail("Inference without durable ledger"), **request())
    assert 1 < len(replacements) <= 4


def test_slow_reservation_write_cannot_start_a_request_after_deadline(tmp_path, monkeypatch):
    now = [0]
    window = ledger(tmp_path, clock=lambda: now[0])
    original = window._save
    def delayed_save():
        original()
        now[0] = 31
    monkeypatch.setattr(window, "_save", delayed_save)
    with pytest.raises(budget.WindowStopped):
        window.invoke("text", lambda **kwargs: pytest.fail("Started after slow reservation"), **request())
