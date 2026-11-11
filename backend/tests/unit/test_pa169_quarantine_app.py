"""PA-169 round 3 (t150): the quarantine follows the report, not the work order.

Round 2 keyed the guard on `input_hash`, which contains the work order id, so a
resident re-filing the same words produced a new hash and walked straight past
the guard. The identity that matters is the content itself.
"""

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.core.errors import AiErrorCode
from app.domain.ai import AiCallStatus, content_fingerprint
from app.provider.local_ai import http_api as api
from app.repository.ai import AiRepository
from app.service.analysis import AnalysisService
from app.service.analysis import orchestrator as orch

ROOT = Path(__file__).resolve().parents[3]

INPUT_HASH = "c" * 64
REPORT_TEXT = "3号楼2单元楼道灯不亮"
CONTENT_FINGERPRINT = content_fingerprint(REPORT_TEXT)
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------- repository
def test_the_quarantine_read_is_one_bounded_probe():
    statements = []

    class _Result:
        def first(self):
            return None

    class _Session:
        def execute(self, statement):
            statements.append(statement)
            return _Result()

    repository = AiRepository(_Session())

    assert (
        repository.quarantined_output_for_content(
            content_fingerprint=CONTENT_FINGERPRINT,
            tenant_id=5,
            error_codes=(AiErrorCode.INVALID_OUTPUT, AiErrorCode.OUTPUT_UNVERIFIABLE),
            since=datetime(2026, 11, 10, tzinfo=UTC),
        )
        is None
    )

    compiled = str(statements[0].compile(compile_kwargs={"literal_binds": False}))
    # One read, bounded to a single row: a retry must not scan the call log.
    assert "LIMIT" in compiled.upper()
    assert "ai_call_logs.content_fingerprint" in compiled
    assert "ai_call_logs.error_code IN" in compiled
    # The window is part of the predicate, not a filter applied afterwards.
    assert "ai_call_logs.created_at >=" in compiled
    # PA-169 round 3: the per-work-order pair must no longer decide anything.
    # `work_order_id` is still selected so the refusal can name the earlier
    # report, but it must never appear as a predicate.
    assert "ai_call_logs.input_hash" not in compiled
    assert "ai_call_logs.work_order_id =" not in compiled
    assert "ai_call_logs.work_order_id IN" not in compiled
    # PA-169 round 4: the content identity is scoped, never global.
    assert "ai_call_logs.tenant_id" in compiled


# ------------------------------------------------------------------- service
def _snapshot(text=REPORT_TEXT, work_order_id=7):
    return SimpleNamespace(
        work_order_id=work_order_id,
        input_hash=INPUT_HASH,
        skipped=False,
        tenant_id=5,
        property_id=5,
        analysis_id=31,
        request_id="req-150",
        text=text,
        images=(),
        audio=(),
    )


def _service(*, force_retry_actor=None, quarantine=None, snapshot=None):
    service = object.__new__(AnalysisService)
    service._session = None
    service._force_retry_actor = force_retry_actor
    service._task_context = None
    service._clock = lambda: NOW
    service._llm = SimpleNamespace(model_version="synthetic-model")
    prepared = snapshot or _snapshot()
    service._prepare = lambda **_: prepared
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **_: quarantine,
        get_current_analysis=lambda _id: None,
    )
    calls = {"logs": [], "applied": [], "providers": 0, "warnings": [], "notes": []}
    service._work_orders = SimpleNamespace(
        get_work_order=lambda _id: SimpleNamespace(
            id=prepared.work_order_id,
            tenant_id=prepared.tenant_id,
            property_id=prepared.property_id,
            status="PENDING_ANALYSIS",
            process_reason="NONE",
        ),
        add_log=lambda record: calls["notes"].append(record) or record,
    )

    def _write_call_log(snapshot, **kwargs):
        calls["logs"].append(kwargs)

    def _apply(*, snapshot, bundle, request_id, attempt, quarantine=None):
        calls["applied"].append(bundle)
        return SimpleNamespace(status="FAILED", error_code=bundle.error_code)

    def _call_providers(**kwargs):
        calls["providers"] += 1
        return SimpleNamespace(
            asr_text=None,
            vision_text=None,
            llm_payload=None,
            error_code=None,
            call_status=AiCallStatus.SUCCESS.value,
        )

    def _warning(event, **kwargs):
        calls["warnings"].append((event, kwargs))

    service._write_call_log = _write_call_log
    service._apply = _apply
    service._call_providers = _call_providers
    original_boundary = orch.shared.begin_owned_transaction
    original_warning = orch.logger.warning
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    orch.logger.warning = _warning
    try:
        analysis = service.analyze_work_order(
            work_order_id=prepared.work_order_id,
            request_id="req-150",
            force_retry_actor=force_retry_actor,
        )
    finally:
        orch.logger.warning = original_warning
        orch.shared.begin_owned_transaction = original_boundary
    return analysis, calls


def _refused_at(previous_work_order_id=7, at=datetime(2026, 11, 11, 3, 0, tzinfo=UTC)):
    return (at, previous_work_order_id)


def test_a_quarantined_report_is_refused_before_the_provider_is_asked():
    analysis, calls = _service(quarantine=_refused_at())

    assert analysis.error_code == AiErrorCode.INPUT_QUARANTINED
    # No model call, so no budget: the refusal happens before _call_providers.
    assert calls["providers"] == 0
    assert calls["applied"][0].error_code == AiErrorCode.INPUT_QUARANTINED
    assert calls["applied"][0].call_status == AiCallStatus.INVALID.value
    log = calls["logs"][0]
    assert log["error_code"] == AiErrorCode.INPUT_QUARANTINED
    assert log["status"] is AiCallStatus.INVALID
    assert log["latency_ms"] == 0
    assert calls["warnings"][0][0] == "analysis_input_quarantined"
    warning = calls["warnings"][0][1]
    assert warning["skipped"] == "quarantined"
    assert warning["reason_code"] == AiErrorCode.INPUT_QUARANTINED
    assert warning["quarantined_since"].startswith("2026-11-11T03:00")
    assert warning["previous_work_order_id"] == 7
    # PA-169 round 4 (F4): the quarantine is also a work-order row the queue shows.
    assert [item.event_code for item in calls["notes"]] == ["AI_QUARANTINE"]
    assert calls["notes"][0].payload_json["previous_work_order_id"] == 7


def test_the_refusal_reaches_a_work_order_that_never_saw_the_text():
    """The whole point of round 3: a re-filed report is a different work order."""

    analysis, calls = _service(
        quarantine=_refused_at(previous_work_order_id=172),
        snapshot=_snapshot(work_order_id=173),
    )

    assert analysis.error_code == AiErrorCode.INPUT_QUARANTINED
    assert calls["providers"] == 0
    warning = calls["warnings"][0][1]
    assert warning["work_order_id"] == 173
    assert warning["previous_work_order_id"] == 172


def test_an_operator_retry_reaches_the_provider_and_is_attributed():
    analysis, calls = _service(force_retry_actor="user:9", quarantine=_refused_at())

    assert calls["providers"] == 1
    assert calls["logs"] == []
    assert analysis.error_code is None
    forced = [item for item in calls["warnings"] if item[0] == "analysis_input_quarantine_forced"]
    assert forced and forced[0][1]["force_retry_actor"] == "user:9"


def test_a_runtime_failure_does_not_quarantine_because_the_repository_says_so():
    analysis, calls = _service(quarantine=None)

    assert calls["providers"] == 1
    assert analysis.error_code is None


def test_the_quarantine_probe_asks_by_content_inside_a_24h_window():
    seen = {}

    service = object.__new__(AnalysisService)
    service._session = None
    service._clock = lambda: NOW
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **kwargs: seen.update(kwargs) or None
    )

    # The probe wraps itself in an owned transaction; a bare test session cannot
    # provide one, so the boundary is stubbed (the real one is covered by e2e).
    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    try:
        assert service._quarantine_for(_snapshot()) is None
    finally:
        orch.shared.begin_owned_transaction = original

    assert seen["content_fingerprint"] == CONTENT_FINGERPRINT
    assert tuple(seen["error_codes"]) == (
        AiErrorCode.INVALID_OUTPUT,
        AiErrorCode.OUTPUT_UNVERIFIABLE,
    )
    assert seen["since"] == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert seen["tenant_id"] == 5
    assert seen["property_id"] == 5


def test_the_probe_reports_when_and_where_the_content_was_refused():
    service = object.__new__(AnalysisService)
    service._session = None
    service._clock = lambda: NOW
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **_: _refused_at(previous_work_order_id=172)
    )

    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    try:
        details = service._quarantine_for(_snapshot())
    finally:
        orch.shared.begin_owned_transaction = original

    assert details == {
        "content_fingerprint": CONTENT_FINGERPRINT,
        "quarantined_since": "2026-11-11T03:00:00+00:00",
        "previous_work_order_id": 172,
    }


def test_the_fingerprint_ignores_punctuation_and_width_but_not_words():
    from app.domain.ai import content_fingerprint

    assert content_fingerprint(REPORT_TEXT) == content_fingerprint("３号楼２单元楼道灯不亮。")
    assert content_fingerprint(REPORT_TEXT) != content_fingerprint("3号楼2单元楼道灯不亮了")


# --------------------------------------------------------------- operator API
class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_the_operator_header_is_read_and_trimmed():
    actor, error = api.force_retry_actor_from_headers(_Headers({"X-Force-Retry-Actor": "  user:9  "}))

    assert (actor, error) == ("user:9", None)


def test_a_missing_operator_header_is_not_an_error():
    assert api.force_retry_actor_from_headers(_Headers({})) == ("", None)


def test_an_over_long_operator_is_rejected_instead_of_dropped():
    actor, error = api.force_retry_actor_from_headers(
        _Headers({"X-Force-Retry-Actor": "u" * 121})
    )

    assert actor == ""
    assert error == "INVALID_FORCE_RETRY_ACTOR"


def test_the_limit_itself_is_accepted():
    actor, error = api.force_retry_actor_from_headers(
        _Headers({"X-Force-Retry-Actor": "u" * api.MAX_FORCE_RETRY_ACTOR})
    )

    assert error is None
    assert actor == "u" * api.MAX_FORCE_RETRY_ACTOR


def test_the_handler_refuses_the_request_instead_of_retrying_unattributed():
    source = (ROOT / "backend/app/provider/local_ai/http_api.py").read_text(encoding="utf-8")

    # The handler is a three-line call into the seam above; what matters here is
    # that the failure is recorded and answered 400, never silently continued.
    assert "force_actor,actor_error=force_retry_actor_from_headers(self.headers)" in source
    assert "return self.reply(400,{'error':actor_error})" in source
    assert "stage='HTTP_ENVELOPE'" in source
