"""PA-169 round 2 (t148): isolation lives where the answer's usability is known.

The adapter can only judge transport, so in practice it never quarantines
anything; the decision has to sit in the analysis service, which is the only
layer that knows a row failed as AI.INVALID_OUTPUT on this exact input.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.core.errors import AiErrorCode
from app.domain.ai import AiCallStatus
from app.provider.local_ai import http_api as api
from app.repository.ai import AiRepository
from app.service.analysis import AnalysisService
from app.service.analysis import orchestrator as orch

ROOT = Path(__file__).resolve().parents[3]

INPUT_HASH = "c" * 64


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

    assert repository.has_quarantined_output(
        work_order_id=7,
        input_hash=INPUT_HASH,
        error_codes=(AiErrorCode.INVALID_OUTPUT, AiErrorCode.OUTPUT_UNVERIFIABLE),
    ) is False

    compiled = str(statements[0].compile(compile_kwargs={"literal_binds": False}))
    # One read, bounded to a single row: a retry must not scan the call log.
    assert "LIMIT" in compiled.upper()
    assert "ai_call_logs.input_hash" in compiled
    assert "ai_call_logs.work_order_id" in compiled
    assert "ai_call_logs.error_code IN" in compiled


# ------------------------------------------------------------------- service
def _snapshot():
    return SimpleNamespace(
        work_order_id=7,
        input_hash=INPUT_HASH,
        skipped=False,
        tenant_id=5,
        property_id=5,
        analysis_id=31,
        request_id="req-148",
    )


def _service(*, force_retry_actor=None, quarantined=True):
    service = object.__new__(AnalysisService)
    service._session = None
    service._force_retry_actor = force_retry_actor
    service._task_context = None
    service._clock = lambda: datetime(2026, 11, 11, tzinfo=UTC)
    service._llm = SimpleNamespace(model_version="synthetic-model")
    snapshot = _snapshot()
    service._prepare = lambda **_: snapshot
    service._ai = SimpleNamespace(
        has_quarantined_output=lambda **_: quarantined,
        get_current_analysis=lambda _id: None,
    )
    original_boundary = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: __import__("contextlib").nullcontext()
    calls = {"logs": [], "applied": [], "providers": 0, "warnings": []}

    def _write_call_log(snapshot, **kwargs):
        calls["logs"].append(kwargs)

    def _apply(*, snapshot, bundle, request_id, attempt):
        calls["applied"].append(bundle)
        return SimpleNamespace(status="FAILED", error_code=bundle.error_code)

    def _call_providers(**kwargs):
        calls["providers"] += 1
        return SimpleNamespace(
            asr_text=None, vision_text=None, llm_payload=None,
            error_code=None, call_status=AiCallStatus.SUCCESS.value,
        )

    def _warning(event, **kwargs):
        calls["warnings"].append((event, kwargs))

    service._write_call_log = _write_call_log
    service._apply = _apply
    service._call_providers = _call_providers
    original_warning = orch.logger.warning
    orch.logger.warning = _warning
    try:
        analysis = service.analyze_work_order(
            work_order_id=7,
            request_id="req-148",
            force_retry_actor=force_retry_actor,
        )
    finally:
        orch.logger.warning = original_warning
        orch.shared.begin_owned_transaction = original_boundary
    return analysis, calls


def test_a_quarantined_input_is_refused_before_the_provider_is_asked():
    analysis, calls = _service()

    assert analysis.error_code == AiErrorCode.INPUT_QUARANTINED
    # No model call, so no budget: the refusal happens before _call_providers.
    assert calls["providers"] == 0
    assert calls["applied"][0].error_code == AiErrorCode.INPUT_QUARANTINED
    assert calls["applied"][0].call_status == AiCallStatus.INVALID.value
    log = calls["logs"][0]
    assert log["error_code"] == AiErrorCode.INPUT_QUARANTINED
    assert log["status"] is AiCallStatus.INVALID
    assert log["latency_ms"] == 0
    assert ("analysis_input_quarantined", ) == (calls["warnings"][0][0], )
    assert calls["warnings"][0][1]["skipped"] == "quarantined"
    assert calls["warnings"][0][1]["reason_code"] == AiErrorCode.INPUT_QUARANTINED


def test_an_operator_retry_reaches_the_provider_and_is_attributed():
    analysis, calls = _service(force_retry_actor="user:9")

    assert calls["providers"] == 1
    assert calls["logs"] == []
    assert analysis.error_code is None
    forced = [item for item in calls["warnings"] if item[0] == "analysis_input_quarantine_forced"]
    assert forced and forced[0][1]["force_retry_actor"] == "user:9"


def test_a_runtime_failure_does_not_quarantine_because_the_repository_says_so():
    analysis, calls = _service(quarantined=False)

    assert calls["providers"] == 1
    assert analysis.error_code is None


def test_the_quarantine_read_uses_only_the_unusable_answer_codes():
    seen = {}

    service = object.__new__(AnalysisService)
    service._session = None
    service._ai = SimpleNamespace(
        has_quarantined_output=lambda **kwargs: seen.update(kwargs) or False
    )

    # The probe wraps itself in an owned transaction; a bare test session cannot
    # provide one, so the boundary is stubbed (the real one is covered by e2e).
    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: __import__("contextlib").nullcontext()
    try:
        assert service._input_is_quarantined(_snapshot()) is False
    finally:
        orch.shared.begin_owned_transaction = original
    assert seen["work_order_id"] == 7
    assert seen["input_hash"] == INPUT_HASH
    assert tuple(seen["error_codes"]) == (
        AiErrorCode.INVALID_OUTPUT,
        AiErrorCode.OUTPUT_UNVERIFIABLE,
    )


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
