"""Restricted automatic retirement of a stale RAG Ollama receipt (t223).

The launcher used to deadlock on receipts whose process was already gone: it
demanded a ``REAL-AI-INTEGRATION-027`` resolution for a port nobody held. The
retirement is now automatic, but only when the machine proves all three
conditions at once:

1. the pid recorded in the receipt no longer exists,
2. nothing listens on the RAG Ollama port,
3. the executable in use is byte-for-byte the build the receipt recorded.

The decision lives in ``deploy/rag-receipt-decision.ps1`` so the four cases can
be exercised without starting a model; the launcher gathers the evidence and
acts on the verdict.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DECISION = ROOT / "deploy/rag-receipt-decision.ps1"
LAUNCHER = ROOT / "deploy/rag-ollama-runtime.ps1"
PINNED = "D571E4CF0AD4456FCCD33D5B985B6D63BA5669C6E5A812391704083952AAE3D5"


def _shell() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required for this launcher")
    return shell


def _decide(tmp_path: Path, *, pid_exists: bool, listeners: int, exe_sha: str, recorded_sha: str | None = PINNED) -> dict:
    receipt = {
        "operation_id": "e880c64fdaa24e79bc5cb3d04bc2dcde",
        "pid": 35976,
        "started_at_utc": "2026-11-12T11:17:43.5022440Z",
        "executable_sha256": recorded_sha,
    }
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    harness = tmp_path / "decide.ps1"
    harness.write_text(
        "\n".join(
            [
                '$ErrorActionPreference = "Stop"',
                f'. "{DECISION}"',
                '$state = Get-Content -Raw -LiteralPath (Join-Path $env:TEST_ROOT "receipt.json") | ConvertFrom-Json',
                "$decision = Get-StaleReceiptDecision -State $state "
                # [bool]"false" is $true in PowerShell, so parse explicitly
                "-RecordedPidExists ([bool]::Parse($env:TEST_PID_EXISTS)) "
                "-ListenerCount ([int]$env:TEST_LISTENERS) "
                "-CurrentExecutableSha256 $env:TEST_EXE_SHA "
                "-ExpectedExecutableSha256 $env:TEST_PINNED_SHA",
                "$decision | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath (Join-Path $env:TEST_ROOT 'decision.json') -Encoding UTF8",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        "TEST_ROOT": str(tmp_path),
        "TEST_PID_EXISTS": "true" if pid_exists else "false",
        "TEST_LISTENERS": str(listeners),
        "TEST_EXE_SHA": exe_sha,
        "TEST_PINNED_SHA": PINNED,
        "SystemRoot": str(Path("C:/Windows")),
    }
    result = subprocess.run(
        [_shell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads((tmp_path / "decision.json").read_text(encoding="utf-8-sig"))


def test_all_three_conditions_retire_the_receipt(tmp_path):
    decision = _decide(tmp_path, pid_exists=False, listeners=0, exe_sha=PINNED)
    assert decision["retire"] is True
    assert decision["reasons"] == []
    evidence = decision["evidence"]
    assert evidence["recorded_pid_exists"] is False
    assert evidence["listener_count"] == 0
    assert evidence["current_executable_sha256"] == PINNED
    assert evidence["receipt_executable_sha256"] == PINNED


def test_live_pid_keeps_the_refusal(tmp_path):
    decision = _decide(tmp_path, pid_exists=True, listeners=0, exe_sha=PINNED)
    assert decision["retire"] is False
    assert any("pid still exists" in reason for reason in decision["reasons"])


def test_listener_on_the_port_keeps_the_refusal(tmp_path):
    decision = _decide(tmp_path, pid_exists=False, listeners=1, exe_sha=PINNED)
    assert decision["retire"] is False
    assert any("listener" in reason for reason in decision["reasons"])


def test_changed_executable_keeps_the_refusal(tmp_path):
    decision = _decide(
        tmp_path,
        pid_exists=False,
        listeners=0,
        exe_sha="CC00918E2D25577969C929A1004BE054781B8BFF25819C1BE19CA2F7B7BB4012",
    )
    assert decision["retire"] is False
    assert any("pinned RAG Ollama build" in reason for reason in decision["reasons"])
    assert any("executable changed" in reason for reason in decision["reasons"])


def test_launcher_wires_the_audit_and_the_fallback():
    text = LAUNCHER.read_text(encoding="utf-8-sig")
    assert "RAG_OLLAMA_RECEIPT_AUTO_RETIRED" in text
    assert "Retire-AutomaticStaleReceipt $state" in text
    assert "rag-receipt-decision.ps1" in text
    # the refusal must say which condition is missing, not just "refused"
    assert "automatic retire missing" in text
    # the explicit authorized resolution stays available for anything the three
    # conditions do not cover
    assert "Retire-AuthorizedStaleReceipt $state" in text
