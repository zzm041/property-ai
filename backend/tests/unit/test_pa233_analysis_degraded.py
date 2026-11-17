"""PA-233: a refused observation must not erase a usable answer.

Before this, one unbound observation (the 4B labelled a lock fault 敲门按铃)
failed the whole analysis: the order stayed PENDING_ANALYSIS, its category
stayed OTHER, and nothing could tell "the model concluded other" from "the
model never concluded anything". The switch keeps PA-209's fail-closed default
byte for byte; with it on, the four dispatch fields survive as a degraded
analysis that cannot rank an employee.
"""
from types import SimpleNamespace

import pytest

from app.service.analysis.validation import ValidationMixin

CATALOG = {"report:1": {"text": "电梯门锁卡异物，门锁打不开", "source": "report"}}
PAYLOAD = {
    "category": "security",
    "severity": "medium",
    "priority": "p2",
    "summary": "门锁卡异物",
    "risk": "other",
    "required_role": "STAFF",
    "required_skill": "maintenance",
    "confidence": 0.85,
    "observations": [{"label": "敲门按铃", "subject": "房门", "evidence_id": "report:1"}],
}


class _Validator(ValidationMixin):
    def __init__(self, *, fallback: bool) -> None:
        self._settings = SimpleNamespace(
            ai_observation_fallback=fallback,
            ai_confidence_threshold=0.70,
            llm_provider="development",
        )


def test_unbound_observations_still_fail_closed_when_the_switch_is_off():
    mapped, error = _Validator(fallback=False)._validate_payload(
        PAYLOAD, evidence_catalog=CATALOG, request_id="pa233-off",
    )
    assert mapped is None
    assert error == "AI.INVALID_OUTPUT"


def test_unbound_observations_degrade_instead_of_failing_when_the_switch_is_on():
    mapped, error = _Validator(fallback=True)._validate_payload(
        PAYLOAD, evidence_catalog=CATALOG, request_id="pa233-on",
    )
    assert error is None
    assert mapped is not None
    # The answer the model did give is kept: category/severity/priority/risk.
    assert mapped.category.value == "SECURITY"
    assert mapped.severity.value == "MEDIUM"
    # p2 maps to HIGH through _PRIORITY_ALIASES (app/domain/ai/rules.py:1217);
    # the assertion pins that mapping so a later alias edit is visible here.
    assert mapped.priority.value == "HIGH"
    assert mapped.risk.value == "other"
    assert mapped.confidence == pytest.approx(0.85)
    # Nothing was proven, so it may not rank an employee or dispatch.
    assert mapped.low_confidence is True
    assert mapped.dispatch_fields_applied is False
    trace = mapped.event_trace
    assert trace["observation_mode"] == "degraded_unbound"
    assert trace["degraded_reason"] == "label_not_matched"
    assert trace["effective_observations"] == []
    assert trace["clarification_reason"] == "NO_CONFIRMED_OBSERVATION"
    # The mislabel itself is preserved, not silently dropped.
    unbound = trace["unbound_observations"]
    assert [item["label"] for item in unbound] == ["敲门按铃"]
    assert unbound[0]["evidence_id"] == "report:1"
    assert unbound[0]["reason_code"] == "label_not_matched"
    assert trace["raw_model_output"]["observations"] == PAYLOAD["observations"]

BOUND_CATALOG = {"report:9": {"text": "厨房水管一直在漏水，地面已经积水", "source": "report"}}


def test_a_bound_analysis_is_untouched_by_the_switch():
    """The switch must only touch the refusal path, never a proven observation."""
    payload = dict(PAYLOAD)
    payload["observations"] = [
        {"label": "滴渗水", "subject": "未确定", "evidence_id": "report:9"},
    ]
    mapped, error = _Validator(fallback=True)._validate_payload(
        payload, evidence_catalog=BOUND_CATALOG, request_id="pa233-bound",
    )
    assert error is None
    assert mapped is not None
    trace = mapped.event_trace
    assert "degraded_reason" not in trace
    assert trace["effective_observations"]
    assert mapped.dispatch_fields_applied is True
    assert mapped.low_confidence is False

def test_the_queue_names_an_analysis_failure_and_drops_it_after_a_retry():
    """PA-233: a failed analysis used to look identical to a pending one."""
    from datetime import datetime, timezone

    from app.repository.work_order import WorkOrderRepository

    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    rows = [
        (11, "AI_ANALYSIS_FAILED", {"reason_code": "AI.INVALID_OUTPUT"}, now, 50),
        (12, "AI_ANALYSIS_FAILED", {"reason_code": "AI.UNAVAILABLE"}, now, 49),
    ]
    answered = [(12, 60)]

    class _Result:
        def __init__(self, payload):
            self._payload = payload

        def all(self):
            return self._payload

    class _Session:
        def __init__(self):
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            return _Result(rows if self.calls == 1 else answered)

    notes = WorkOrderRepository(_Session()).quarantine_notes([11, 12])

    # Order 11 has no later ANALYZE log, so its refusal stays visible.
    assert "AI.INVALID_OUTPUT" in notes[11]
    assert "force_retry" in notes[11]
    # Order 12 was re-analysed after the failure: the stale note is gone.
    assert 12 not in notes
