"""PA-179 (t191): reviewed evidence must stop consuming the live budget.

The tests drive ``scripts/ops/ai_evidence_archive.py`` directly: the point of the
tool is that a declaration is inspectable (ledger line with a pinned sha256) and
reversible-by-record rather than by deletion, so most of these assertions are
about what ends up on disk.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "scripts" / "ops" / "ai_evidence_archive.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("ai_evidence_archive", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tool():
    return load_tool()


def make_call(runtime: Path, name: str, *, kind: str = "qwen", status: str = "SUCCESS", reason=None) -> Path:
    call_dir = runtime / "calls" / name
    call_dir.mkdir(parents=True)
    (call_dir / "reservation.json").write_text(
        json.dumps(
            {
                "kind": kind,
                "model": "qwen3.5:4b",
                "input_hash": hashlib.sha256(name.encode()).hexdigest(),
                "utc": "2026-11-11T14:00:00Z",
                "budget_counted": True,
            }
        ),
        encoding="utf-8",
    )
    (call_dir / "result.json").write_text(
        json.dumps({"status": status, "reason": reason, "total_seconds": 1.5}), encoding="utf-8"
    )
    (call_dir / "input.json").write_text(json.dumps({"kind": kind, "value": {"model": "qwen3.5:4b"}}), encoding="utf-8")
    return call_dir


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "delivery-work-ai-runtime"
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps({"limits": {"qwen": 2}, "call_baseline": {"qwen": 0}, "duration_seconds": 1800}),
        encoding="utf-8",
    )
    make_call(root, "call-001")
    make_call(root, "call-002")
    return root


def test_archive_moves_one_call_out_of_the_counted_side(tool, runtime):
    result = tool.archive(call="call-001", reason="t165 review evidence", runtime=runtime)

    assert result["status"] == "archived"
    assert result["config_changed"] is True
    assert result["restart_required"] is True
    assert result["budget_before"]["counted"] == {"qwen": 2}
    assert result["budget_after"]["counted"] == {"qwen": 1}
    assert result["budget_after"]["evidence_records"] == ["call-001"]


def test_config_only_gains_the_evidence_key(tool, runtime):
    tool.archive(call="call-002", reason="kept for review", runtime=runtime)

    config = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    assert config["evidence_records"] == ["call-002"]
    # the limits are what would actually widen the budget; archiving must not touch them
    assert config["limits"] == {"qwen": 2}
    assert config["call_baseline"] == {"qwen": 0}
    assert config["duration_seconds"] == 1800


def test_ledger_line_carries_sha256_and_identity(tool, runtime):
    result = tool.archive(call="call-001", reason="reviewed", runtime=runtime)

    ledger = runtime / "evidence-ledger.jsonl"
    assert ledger.is_file()
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["call"] == "call-001"
    assert entry["reason"] == "reviewed"
    assert entry["record_sha256"] == result["record_sha256"]
    assert len(entry["record_sha256"]) == 64
    assert set(entry["files"]) == {"reservation.json", "result.json", "input.json"}
    for name, digest in entry["files"].items():
        assert digest == tool.sha256_file(runtime / "calls" / "call-001" / name)
    assert entry["details"]["input_hash"] == json.loads(
        (runtime / "calls" / "call-001" / "reservation.json").read_text(encoding="utf-8")
    )["input_hash"]


def test_re_archiving_an_unchanged_call_is_a_no_op(tool, runtime):
    first = tool.archive(call="call-001", reason="reviewed", runtime=runtime)
    second = tool.archive(call="call-001", reason="reviewed", runtime=runtime)

    assert first["status"] == "archived"
    assert second["status"] == "already-archived"
    assert second["config_changed"] is False
    assert second["restart_required"] is False
    lines = (runtime / "evidence-ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if line.strip()]) == 1


def test_a_changed_call_is_superseded_instead_of_rewritten(tool, runtime):
    first = tool.archive(call="call-001", reason="reviewed", runtime=runtime)
    (runtime / "calls" / "call-001" / "result.json").write_text(
        json.dumps({"status": "FAILED", "reason": "TIMEOUT", "total_seconds": 30}), encoding="utf-8"
    )
    second = tool.archive(call="call-001", reason="re-reviewed after the result changed", runtime=runtime)

    assert second["status"] == "archived"
    assert second["record_sha256"] != first["record_sha256"]
    entries = [json.loads(line) for line in (runtime / "evidence-ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2
    assert entries[1]["supersedes"] == first["record_sha256"]


def test_archiving_never_touches_the_call_files(tool, runtime):
    before = {
        name: (runtime / "calls" / "call-001" / name).read_bytes()
        for name in ("reservation.json", "result.json", "input.json")
    }
    tool.archive(call="call-001", reason="reviewed", runtime=runtime)
    after = {
        name: (runtime / "calls" / "call-001" / name).read_bytes()
        for name in ("reservation.json", "result.json", "input.json")
    }
    assert before == after


def test_a_partial_record_is_refused(tool, runtime):
    (runtime / "calls" / "call-002" / "result.json").unlink()
    with pytest.raises(tool.ArchiveRefused) as excinfo:
        tool.archive(call="call-002", reason="reviewed", runtime=runtime)
    assert "missing result.json" in str(excinfo.value)
    assert not (runtime / "evidence-ledger.jsonl").exists()


@pytest.mark.parametrize("call", ["", "call-999", "../config.json", "call-001/../../config.json", "config.json"])
def test_unknown_or_traversing_calls_are_refused(tool, runtime, call):
    with pytest.raises(tool.ArchiveRefused):
        tool.archive(call=call, reason="reviewed", runtime=runtime)


def test_dry_run_reports_without_writing(tool, runtime):
    result = tool.archive(call="call-001", reason="reviewed", runtime=runtime, dry_run=True)

    assert result["status"] == "dry-run"
    assert result["budget_after"]["counted"] == {"qwen": 1}
    assert not (runtime / "evidence-ledger.jsonl").exists()
    assert "evidence_records" not in json.loads((runtime / "config.json").read_text(encoding="utf-8"))


def test_list_reports_declared_state(tool, runtime):
    tool.archive(call="call-001", reason="reviewed", runtime=runtime)
    entries = tool.list_ledger(runtime)

    assert [entry["call"] for entry in entries] == ["call-001"]
    assert entries[0]["declared"] is True
    assert len(entries[0]["record_sha256"]) == 64


def test_cli_exit_codes(tool, runtime, capsys):
    assert tool.main(["--runtime-dir", str(runtime), "--call", "call-001", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "archived"

    assert tool.main(["--runtime-dir", str(runtime)]) == 2
    assert "a call id is required" in capsys.readouterr().err

    assert tool.main(["--runtime-dir", str(runtime), "--call", "call-404"]) == 1
    assert "EVIDENCE_ARCHIVE_REFUSED" in capsys.readouterr().err


def test_archive_writes_the_launcher_manifest_not_only_the_config(tool, runtime):
    """work-ai-runtime.ps1 rebuilds config.json from evidence-records.txt on every start."""

    tool.archive(call="call-001", reason="t165 review evidence", runtime=runtime)

    manifest = runtime / "evidence-records.txt"
    assert manifest.is_file()
    assert manifest.read_text(encoding="utf-8") == "call-001\n"


def test_manifest_is_sorted_unique_and_merged_with_the_config_copy(tool, runtime):
    (runtime / "evidence-records.txt").write_text("call-003\n\ncall-002\n", encoding="utf-8")
    tool.archive(call="call-001", reason="t165 review evidence", runtime=runtime)

    assert (runtime / "evidence-records.txt").read_text(encoding="utf-8") == "call-001\ncall-002\ncall-003\n"
    assert tool.declared_calls(runtime, tool.load_config(runtime)) == ["call-001", "call-002", "call-003"]


def test_a_manifest_declaration_alone_takes_the_call_off_the_counted_side(tool, runtime):
    """The manifest is what the launcher reads, so a hand-written line must count as declared."""

    (runtime / "evidence-records.txt").write_text("call-001\n", encoding="utf-8")

    assert tool.budget_snapshot(runtime, tool.declared_calls(runtime, tool.load_config(runtime)))["counted"] == {"qwen": 1}


def test_manifest_lines_must_be_call_ids(tool, runtime):
    (runtime / "evidence-records.txt").write_text("../config.json\n", encoding="utf-8")

    with pytest.raises(tool.ArchiveRefused):
        tool.read_manifest(runtime)


# --------------------------------------------------------------- reconcile (T194-1)
def test_reconcile_drops_manifest_lines_whose_call_directory_is_gone(tool, runtime):
    # call-002 exists on disk but is not declared; call-050 does not exist at all.
    (runtime / "evidence-records.txt").write_text("call-001\ncall-002\ncall-050\n", encoding="utf-8")
    config = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    config["evidence_records"] = ["call-001", "call-002", "call-050"]
    (runtime / "config.json").write_text(json.dumps(config), encoding="utf-8")
    counted_before = tool.budget_snapshot(runtime, ["call-001", "call-002", "call-050"])["counted"]

    result = tool.reconcile(runtime=runtime, reason="manifest audit")

    assert result["status"] == "reconciled"
    assert (result["declared_before"], result["declared_after"], result["on_disk"]) == (3, 2, 2)
    assert result["ghost_calls"] == ["call-050"]
    assert (runtime / "evidence-records.txt").read_text(encoding="utf-8").split() == ["call-001", "call-002"]
    updated = json.loads((runtime / "config.json").read_text(encoding="utf-8"))
    assert updated["evidence_records"] == ["call-001", "call-002"]
    # Removing a ghost must not move a single spend counter.
    assert result["budget_after"]["counted"] == counted_before
    assert result["budget_after"]["evidence_excluded"] == result["budget_before"]["evidence_excluded"]
    ledger = [json.loads(line) for line in (runtime / "evidence-ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ledger) == 1
    assert ledger[0]["event"] == "reconciled"
    assert ledger[0]["removed"] == ["call-050"]
    assert ledger[0]["declared_before"] == 3 and ledger[0]["declared_after"] == 2

    assert tool.ledger_summary(runtime) == {"declared": 2, "on_disk": 2, "ghosts": 0}


def test_reconcile_is_idempotent_when_the_manifest_is_clean(tool, runtime):
    (runtime / "evidence-records.txt").write_text("call-001\n", encoding="utf-8")

    first = tool.reconcile(runtime=runtime)
    second = tool.reconcile(runtime=runtime)

    assert first["status"] == "clean" and first["ghosts"] == 0
    assert second == first
    assert not (runtime / "evidence-ledger.jsonl").exists()
    assert (runtime / "evidence-records.txt").read_text(encoding="utf-8").split() == ["call-001"]


def test_reconcile_dry_run_reports_ghosts_without_touching_the_manifest(tool, runtime):
    (runtime / "evidence-records.txt").write_text("call-001\ncall-077\n", encoding="utf-8")

    result = tool.reconcile(runtime=runtime, dry_run=True)

    assert result["status"] == "dry-run"
    assert result["ghosts"] == 1 and result["ghost_calls"] == ["call-077"]
    assert result["config_changed"] is True
    assert (runtime / "evidence-records.txt").read_text(encoding="utf-8").split() == ["call-001", "call-077"]
    assert not (runtime / "evidence-ledger.jsonl").exists()


def test_cli_reconcile_prints_the_three_numbers(tool, runtime, capsys):
    (runtime / "evidence-records.txt").write_text("call-001\ncall-404\n", encoding="utf-8")

    assert tool.main(["--runtime-dir", str(runtime), "--reconcile"]) == 0
    printed = capsys.readouterr().out
    assert "declared 2 -> 1" in printed
    assert "on_disk=2" in printed
    assert "ghosts=1" in printed
