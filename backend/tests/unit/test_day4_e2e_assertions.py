"""Negative doubles for Day 4 E2E honesty: no overall PASS on required failures."""

from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "day4_e2e.py"


def _load():
    spec = importlib.util.spec_from_file_location("day4_e2e_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalize_failure_is_nonzero_and_does_not_print_pass() -> None:
    module = _load()
    buffer = StringIO()
    with patch("sys.stdout", buffer):
        code = module.finalize(True, ["assign=409", "receipt=422", "final_status=WAIT_CONFIRM"])
    output = buffer.getvalue()
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "DAY4_CROSS_PROCESS_E2E=FAIL" in output


def test_finalize_success_requires_no_failures() -> None:
    module = _load()
    buffer = StringIO()
    with patch("sys.stdout", buffer):
        code = module.finalize(False, ["closed=CLOSED"])
    assert code == 0
    assert module.OVERALL_PASS in buffer.getvalue()


def test_wrong_citation_and_empty_notifications_fail_helpers() -> None:
    module = _load()
    assert not module.citations_match_ingest(
        [{"document_id": 1, "version": 1, "title": "old"}],
        document_id=99,
        version=2,
    )
    assert module.citations_match_ingest(
        [{"document_id": 99, "version": 2, "title": "this round"}],
        document_id=99,
        version=2,
    )
    assert not module.notifications_include(
        [{"resource_type": "WORK_ORDER", "resource_id": 1}],
        resource_type="WORK_ORDER",
        resource_id=99,
    )


def test_checks_collect_assign_receipt_timeout_and_wrong_doc() -> None:
    module = _load()
    checks = module.Checks()
    checks.expect(False, "assign=409")
    checks.expect(False, "receipt=422")
    checks.expect(False, "final_status=WAIT_CONFIRM")
    checks.expect(
        module.citations_match_ingest([{"document_id": 3, "version": 1}], document_id=9, version=2),
        "wrong_document_citation",
    )
    assert checks.failed
    buffer = StringIO()
    with patch("sys.stdout", buffer):
        code = module.finalize(checks.failed, checks.evidence)
    assert code == 1
    assert "PASS" not in buffer.getvalue().split("DAY4_CROSS_PROCESS_E2E=")[-1]


def test_missing_base_url_is_not_run() -> None:
    module = _load()
    with patch.object(module, "BASE", ""):
        buffer = StringIO()
        with patch("sys.stdout", buffer), patch("sys.stderr", StringIO()):
            code = module.main()
    assert code == 2
    assert "NOT_RUN" in buffer.getvalue()
    assert module.OVERALL_PASS not in buffer.getvalue()
