"""PA-130: `up` must converge the runtime binding instead of reporting a stale one.

The behaviour lives in the reviewed PowerShell launchers, so the guard is a source-level
one: if someone removes the convergence (or lets the "already running" branch short-circuit
back to a bare `break`), the failure returns as every request hitting RUNTIME_IDENTITY.
"""

from __future__ import annotations

from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[3] / "deploy" / "work-ai-runtime.ps1"


def _source() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_binding_is_derived_from_the_live_ollama_receipt() -> None:
    source = _source()
    assert "function Get-ExpectedRuntimeBinding {" in source
    # the binding fields the adapter compares: pid / executable / launched_at_utc
    for field in ("pid = [int]$ollama.pid", "executable = [string]$ollama.executable", "launched_at_utc ="):
        assert field in source
    assert "source_receipt_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaCurrent).Hash" in source


def test_binding_comparison_matches_the_runtime_control_contract() -> None:
    source = _source()
    assert "function Test-RuntimeBinding([string]$Path, [object]$Expected)" in source
    # one second tolerance, exactly like local_ai_runtime_control.bound_runtime_identity
    assert "-gt 1000" in source
    for reason in ('"pid"', '"executable"', '"launched_at_utc"', '"source_receipt_sha256"', '"unreadable"'):
        assert reason in source


def test_already_running_branch_converges_before_it_returns() -> None:
    source = _source()
    # the refreshed binding has to be reported either way
    assert "runtime_binding=ok" in source
    assert "runtime_binding=rewritten" in source
    # a stale binding stops the recorded adapter through its receipt and then falls through
    assert "Stop-RecordedAdapter $state" in source
    assert "superseded stale binding:" in source
    # the "already running" branch may only short-circuit when the binding is healthy
    start_branch = source.split('switch ($Action) {', 1)[1]
    already_running = start_branch.split('$existing = @(Get-Listener)', 1)[1].split("$token = Read-Token\n        if (-not $token)", 1)[0]
    assert "$bindingState.ok" in already_running
    assert already_running.index("$bindingState.ok") < already_running.index("break")


def test_start_ends_with_a_binding_self_check() -> None:
    source = _source()
    assert "runtime binding did not converge after start:" in source
    # the self-check runs after the receipt is written, so a failed convergence leaves a receipt
    # that names the binding it verified
    assert source.index("[IO.File]::WriteAllText($CurrentPath") < source.index("runtime binding did not converge after start:")
