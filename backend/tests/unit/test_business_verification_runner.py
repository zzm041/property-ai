"""The review entry point must cover new work without executing on dry-run."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("business_verification_runner", ROOT / "scripts/verify_business_ai.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize("suite,required", [
    ("semantic", "test_observation_coverage_v16.py"),
    ("agent", "test_supervisor_agent_integration.py"),
])
def test_new_suite_dry_run_selects_existing_unit_tests_without_execution(suite, required, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry-run executed a child")
    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    assert runner.main(["--suite", suite]) == 0
    plan = json.loads(capsys.readouterr().out)
    commands = plan["commands"]
    assert plan["mode"] == "PLAN_ONLY"
    assert any(f"backend/tests/unit/{required}" in command for _, command in commands)
    assert all("backend/tests/integration/" not in arg for _, command in commands for arg in command)


@pytest.mark.parametrize("outcome,expected", [(0, "PASS"), (1, "FAIL"), ("timeout", "TIMEOUT"),
    ("modified", "PASS"), ("added", "PASS"), ("deleted", "PASS")])
def test_run_records_outcome_and_uses_owned_temp_directory(tmp_path, monkeypatch, outcome, expected):
    for name in ["backend/tests/unit/example.py", "backend/pyproject.toml", "scripts/verify_business_ai.py"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic fixture")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "SUITES", {"semantic": ("example.py",)})
    def child(command, **kwargs):
        temp_arg = next(arg for arg in command if arg.startswith("--basetemp="))
        temp_path = Path(temp_arg.split("=", 1)[1])
        assert temp_path.is_relative_to(tmp_path / "reports/business-ai-verification")
        kwargs["stdout"].write(b"synthetic child evidence")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        if outcome == "modified":
            (tmp_path / "backend/tests/unit/example.py").write_text("# changed during verification")
            return SimpleNamespace(returncode=0)
        if outcome == "added":
            new = tmp_path / "backend/app/added.py"
            new.parent.mkdir(parents=True)
            new.write_text("# appeared during verification")
            return SimpleNamespace(returncode=0)
        if outcome == "deleted":
            (tmp_path / "backend/tests/unit/example.py").unlink()
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=outcome)
    monkeypatch.setattr(runner.subprocess, "run", child)
    assert runner.main(["--suite", "semantic", "--run"]) == (0 if outcome == 0 else 1)
    paths = list((tmp_path / "reports/business-ai-verification").glob("*/result.json"))
    assert len(paths) == 1
    record = json.loads(paths[0].read_text())
    assert record["results"][0]["status"] == expected
    assert record["status"] == ("PASS" if outcome == 0 else "FAIL")
    if outcome in {"modified", "added", "deleted"}:
        assert record["source_changes"]
    assert (paths[0].parent / "semantic.txt").read_text() == "synthetic child evidence"
