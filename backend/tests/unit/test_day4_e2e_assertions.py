"""Negative doubles for Day 4 E2E honesty: no overall PASS on required failures."""

from __future__ import annotations

import importlib.util
import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "day4_e2e.py"
IDS = {
    "system": 1,
    "manager": 2,
    "resident": 3,
    "staff": 4,
    "house_id": 10,
    "property_id": 20,
    "employee_id": 30,
}


def _load():
    spec = importlib.util.spec_from_file_location("day4_e2e_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict[str, object]:
        return self._payload


class ScriptedClient:
    """HTTP-boundary double for the real `_run_flow` sequence."""

    def __init__(self, fault: str | None = None) -> None:
        self.fault = fault
        self.wo_status = "PENDING_ASSIGNMENT"
        self.confirm_status = "CLOSED"
        self.db_status = "CLOSED"
        self.notification_type = "WORK_ORDER"
        if fault == "timeout":
            self.wo_status = "PENDING_ANALYSIS"
        if fault == "not_closed":
            self.confirm_status = "WAIT_CONFIRM"
            self.db_status = "WAIT_CONFIRM"
        if fault == "wrong_notification":
            self.notification_type = "HOUSE"

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        headers = kwargs.get("headers") or {}
        request_id = ""
        if isinstance(headers, dict):
            request_id = str(headers.get("X-Request-Id") or "")
        if url.endswith("/healthz"):
            return FakeResponse(200, {"status": "ok"})
        if url.endswith("/readyz"):
            status = 503 if self.fault == "readyz_503" else 200
            deps = {
                name: {"status": "not_checked"}
                for name in ("llm", "vlm", "asr", "embedding", "reranker")
            }
            return FakeResponse(status, {"data": {"dependencies": deps}})
        if url.endswith("/opsz"):
            if "d4-opsz-public" in request_id or not (isinstance(headers, dict) and headers.get("Authorization")):
                return FakeResponse(401, {"error": {"code": "AUTH.MISSING_TOKEN"}})
            if "d4-opsz-mgr" in request_id:
                return FakeResponse(403, {"error": {"code": "IAM.FORBIDDEN"}})
            return FakeResponse(200, {"data": {"celery_queue": "property_ai.day4"}})
        if "/me/houses" in url:
            return FakeResponse(200, {"data": {"items": []}})
        if "/staff/attendance/today" in url:
            return FakeResponse(200, {"data": {"daily_status": {"on_duty": "ON_DUTY"}}})
        if "/notifications" in url:
            items = [
                {"resource_type": "RESIDENT_BIND_REQUEST", "resource_id": 77},
                {"resource_type": self.notification_type, "resource_id": 88},
                {"resource_type": "ANNOUNCEMENT", "resource_id": 99},
            ]
            return FakeResponse(200, {"data": {"items": items}})
        if "/work-orders/" in url:
            return FakeResponse(200, {"data": {"id": 88, "status": self.wo_status}})
        return FakeResponse(404, {"error": {"code": "NOT_FOUND"}})

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        headers = kwargs.get("headers") or {}
        request_id = ""
        if isinstance(headers, dict):
            request_id = str(headers.get("X-Request-Id") or "")
        if url.endswith("/auth/dev/token"):
            return FakeResponse(200, {"data": {"access_token": "tok"}})
        if url.endswith("/opsz"):
            return FakeResponse(200, {"data": {"celery_queue": "property_ai.day4"}})
        if url.endswith("/auth/wechat/login"):
            return FakeResponse(200, {"data": {"access_token": "wx-tok"}})
        if url.endswith("/resident-bind-requests"):
            return FakeResponse(200, {"data": {"id": 77, "status": "PENDING"}})
        if url.endswith("/approve"):
            return FakeResponse(200, {"data": {"status": "APPROVED"}})
        if url.endswith("/work-orders"):
            return FakeResponse(200, {"data": {"id": 88, "status": "PENDING_ANALYSIS"}})
        if url.endswith("/checkin"):
            return FakeResponse(200, {"data": {"on_duty": "ON_DUTY"}})
        if url.endswith("/assign"):
            if self.fault == "assign_409":
                return FakeResponse(409, {"error": {"code": "WO.CONFLICT"}})
            return FakeResponse(200, {"data": {"status": "ASSIGNED"}})
        if url.endswith("/accept"):
            return FakeResponse(200, {"data": {"status": "ACCEPTED"}})
        if url.endswith("/start"):
            return FakeResponse(200, {"data": {"status": "PROCESSING"}})
        if url.endswith("/receipts"):
            if self.fault == "receipt_422":
                return FakeResponse(422, {"error": {"code": "WO.INVALID_INPUT"}})
            return FakeResponse(200, {"data": {"id": 1}})
        if url.endswith("/confirm"):
            return FakeResponse(200, {"data": {"status": self.confirm_status}})
        if url.endswith("/knowledge-documents"):
            return FakeResponse(200, {"data": {"document_id": 5, "version": 1}})
        if url.endswith("/assistant/query"):
            return FakeResponse(
                200,
                {"data": {"citations": [{"document_id": 5, "version": 1, "title": "this round"}]}},
            )
        if url.endswith("/announcements"):
            return FakeResponse(200, {"data": {"id": 99, "status": "DRAFT"}})
        if url.endswith("/submit-review"):
            return FakeResponse(200, {"data": {"status": "PENDING_REVIEW"}})
        if url.endswith("/publish"):
            return FakeResponse(200, {"data": {"status": "PUBLISHED"}})
        return FakeResponse(404, {"error": {"code": "NOT_FOUND"}})


class FakeConnect:
    def __init__(self, status: str) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement, params=None):
        del statement

        class Result:
            def __init__(self, value: str) -> None:
                self._value = value

            def scalar(self) -> str:
                return self._value

        return Result(self.status)


class FakeEngine:
    def __init__(self, status: str) -> None:
        self.status = status

    def connect(self) -> FakeConnect:
        return FakeConnect(self.status)

    def dispose(self) -> None:
        return None


def _run_fault(module, fault: str, *, worker_running: bool = False) -> tuple[int, str]:
    checks = module.Checks()
    client = ScriptedClient(fault=fault)
    clock = {"now": 1_000.0}

    def fake_time() -> float:
        clock["now"] += 40
        return clock["now"]

    buffer = StringIO()
    with (
        patch.object(module, "BASE", "http://e2e.test"),
        patch.dict(os.environ, {"TEST_DATABASE_URL": "mysql+pymysql://x/x"}, clear=False),
        patch.object(module, "time") as time_mod,
        patch.object(module, "create_engine", lambda *_a, **_k: FakeEngine(client.db_status)),
        patch.object(module, "subprocess") as subprocess_mod,
        patch("sys.stdout", buffer),
    ):
        time_mod.time.side_effect = fake_time
        time_mod.sleep.return_value = None
        subprocess_mod.run.return_value = MagicMock(returncode=1 if fault == "worker_stop" else 0, stdout="true", stderr="boom")
        module._run_flow(client, IDS, checks, worker_running)
        code = module.finalize(checks.failed, checks.evidence)
    return code, buffer.getvalue()


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
    assert not module.notifications_include(
        [{"resource_type": "HOUSE", "resource_id": 88}],
        resource_type="WORK_ORDER",
        resource_id=88,
    )


def test_script_flow_assign_409_is_nonzero_fail() -> None:
    module = _load()
    code, output = _run_fault(module, "assign_409")
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "assign" in output


def test_script_flow_receipt_failure_is_nonzero_fail() -> None:
    module = _load()
    code, output = _run_fault(module, "receipt_422")
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "receipt" in output


def test_script_flow_wrong_notification_type_same_id_fails() -> None:
    module = _load()
    code, output = _run_fault(module, "wrong_notification")
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "notification_work_order_this_round" in output


def test_script_flow_async_timeout_fails() -> None:
    module = _load()
    code, output = _run_fault(module, "timeout")
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "wo_after_analysis" in output or "stayed PENDING_ANALYSIS" in output


def test_script_flow_final_not_closed_fails() -> None:
    module = _load()
    code, output = _run_fault(module, "not_closed")
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "final_status=WAIT_CONFIRM" in output or "db_work_order_status=WAIT_CONFIRM" in output


def test_script_flow_worker_stop_command_failure_fails() -> None:
    module = _load()
    code, output = _run_fault(module, "worker_stop", worker_running=True)
    assert code == 1
    assert module.OVERALL_PASS not in output
    assert "worker_stop_rc" in output


def test_missing_base_url_is_not_run() -> None:
    module = _load()
    with patch.object(module, "BASE", ""):
        buffer = StringIO()
        with patch("sys.stdout", buffer), patch("sys.stderr", StringIO()):
            code = module.main()
    assert code == 2
    assert "NOT_RUN" in buffer.getvalue()
    assert module.OVERALL_PASS not in buffer.getvalue()
