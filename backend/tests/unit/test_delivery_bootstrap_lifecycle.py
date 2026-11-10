"""Exercise the actual launcher in a temporary tree with Docker replaced by a recorder."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("action, succeeds", [("config", True), ("up", True), ("status", True), ("init", False), ("media-cycle-migrate", False)])
def test_bootstrap_file_is_only_required_for_initialization(tmp_path, action, succeeds):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required for this launcher")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy2(ROOT / "deploy/delivery.ps1", deploy / "delivery.ps1")
    # PA-170 (t147): delivery.ps1 dot-sources the line-ending independent digest
    # helper, so the temporary tree needs it too.
    shutil.copy2(ROOT / "deploy/source-hash.ps1", deploy / "source-hash.ps1")
    (deploy / ".env.delivery").write_text("APP_ENV=staging\n", encoding="utf-8")
    harness = tmp_path / "check.ps1"
    harness.write_text('''$ErrorActionPreference = "Stop"
function docker { $global:LASTEXITCODE = 0 }
try { & (Join-Path $env:TEST_DELIVERY_ROOT "deploy/delivery.ps1") $env:TEST_DELIVERY_ACTION; exit 0 }
catch { Write-Output $_.Exception.Message; exit 1 }
''', encoding="utf-8")
    env = dict(os.environ, TEST_DELIVERY_ROOT=str(tmp_path), TEST_DELIVERY_ACTION=action)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert (result.returncode == 0) == succeeds, f"unexpected bootstrap requirement for {action}"
    if not succeeds:
        assert "bootstrap" in result.stdout.lower()


@pytest.mark.parametrize("action, needs_overlay", [("init", True), ("up", False)])
def test_launcher_injects_bootstrap_overlay_only_for_migration(tmp_path, action, needs_overlay):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required for this launcher")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy2(ROOT / "deploy/delivery.ps1", deploy / "delivery.ps1")
    shutil.copy2(ROOT / "deploy/source-hash.ps1", deploy / "source-hash.ps1")
    (deploy / ".env.delivery").write_text("APP_ENV=staging\n", encoding="utf-8")
    (deploy / ".env.delivery.bootstrap").write_text("SYNTHETIC=true\n", encoding="utf-8")
    harness = tmp_path / "check.ps1"
    harness.write_text('''$ErrorActionPreference = "Stop"
$global:RecordedCommands = @()
function docker {
    $global:RecordedCommands += ,@($args)
    $global:LASTEXITCODE = 0
}
& (Join-Path $env:TEST_DELIVERY_ROOT "deploy/delivery.ps1") $env:TEST_DELIVERY_ACTION
$global:RecordedCommands | ConvertTo-Json -Depth 5 -Compress | Set-Content (Join-Path $env:TEST_DELIVERY_ROOT "commands.json")
''', encoding="utf-8")
    env = dict(os.environ, TEST_DELIVERY_ROOT=str(tmp_path), TEST_DELIVERY_ACTION=action)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    import json
    calls = json.loads((tmp_path / "commands.json").read_text(encoding="utf-8-sig"))
    # Windows PowerShell serialises the captured argument list as
    # {"value": [...], "Count": n}; PowerShell 7 emits a bare array.
    calls = [call["value"] if isinstance(call, dict) else call for call in calls]
    compose_calls = [call for call in calls if call and call[0] == "compose"]
    assert compose_calls
    for call in compose_calls:
        overlay = str(deploy / "docker-compose.bootstrap.yml")
        assert (overlay in call) == needs_overlay
        if needs_overlay:
            assert call[call.index(overlay) - 1] == "-f"
