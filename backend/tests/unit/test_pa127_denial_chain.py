"""PA-127: close the three gaps left over from the PA-101-A explanation work.

F1 - the two pre-checks in `_assign` (unknown employee, employee from another
     tenant) raised the shared 409 with an empty `details` payload.
F2 - `capability_note` was only used by the manual gate; the auto-dispatch
     snapshot and the recommendation rows stored `filter_reason` alone.
F3 - nothing consumed `details.reason` (log line and phone copy).

The tests drive the real production functions with stubbed collaborators so the
reason strings and their placement are asserted without a database.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings
from app.core.time import utc_now

from app.domain.ai import (
    SKILL_UNCONSTRAINED_OTHER,
    FilterReason,
    MappedAnalysis,
    capability_note,
    eligibility_denial_details,
)
from app.core.errors import WorkOrderErrorCode
from app.service.analysis import AnalysisService
from app.service.auto_dispatch import AutoDispatchService
from app.service.work_order import (
    WorkOrderService,
    _employee_scope_denial,
    eligibility_denied,
)

DUTY = "MAINTENANCE"
UNCONSTRAINED_SKILL = "OTHER"
PLUMBING_SKILL = "PLUMBING"


def _employee(tenant_id: int, *, employee_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        id=employee_id,
        tenant_id=tenant_id,
        user_id=employee_id + 100,
        status="ACTIVE",
        max_capacity=3,
    )


def _mapped(*, required_duty: str | None, required_skill: str | None) -> MappedAnalysis:
    return MappedAnalysis(
        category="PLUMBING",
        subcategory="water_supply",
        severity="LOW",
        priority="MEDIUM",
        summary="卫生间水龙头滴水。",
        risk="none",
        required_role="STAFF",
        required_skill=required_skill or UNCONSTRAINED_SKILL,
        required_duty=required_duty,
        confidence=0.95,
        low_confidence=False,
    )


# ---------------------------------------------------------------- F1 scope gate


def test_unknown_employee_is_a_409_with_a_reason() -> None:
    denial = _employee_scope_denial(None, tenant_id=1)

    assert denial is not None
    assert denial.code == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE
    assert denial.status_code == 409
    assert denial.details == eligibility_denial_details("EMPLOYEE_NOT_FOUND"), (
        "the unknown-employee branch must not stay a bare 409"
    )


def test_foreign_employee_is_a_409_with_a_reason() -> None:
    denial = _employee_scope_denial(_employee(tenant_id=2), tenant_id=1)

    assert denial is not None
    assert denial.details["reason"] == "TENANT_MISMATCH"


def test_same_tenant_employee_passes_the_scope_precheck() -> None:
    assert _employee_scope_denial(_employee(tenant_id=1), tenant_id=1) is None


def test_shared_factory_keeps_capability_context() -> None:
    denial = eligibility_denied(
        reason=FilterReason.DUTY_MISMATCH.value,
        required_duty=DUTY,
        required_skill=UNCONSTRAINED_SKILL,
    )

    assert denial.code == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE
    assert denial.status_code == 409
    assert denial.details == eligibility_denial_details(
        FilterReason.DUTY_MISMATCH.value,
        required_duty=DUTY,
        required_skill=UNCONSTRAINED_SKILL,
    )


def test_roster_gate_still_reports_the_tenant_reason() -> None:
    """The in-gate tenant branch stays reachable for callers that skip the pre-check."""

    service = object.__new__(WorkOrderService)
    record = SimpleNamespace(tenant_id=1)
    error = service._eligibility_error(record, _employee(tenant_id=2))

    assert error is not None
    assert error.details["reason"] == "TENANT_MISMATCH"


# ------------------------------------------------------- F2 auto-dispatch rows


class _StubDispatchAi:
    def __init__(self, *, duties: frozenset[str], skills: frozenset[str]) -> None:
        self._duties = duties
        self._skills = skills

    def get_building_id(self, house_id: int) -> int | None:  # noqa: ARG002 - stub
        return 11

    def list_property_employees(self, *, tenant_id: int, property_id: int):  # noqa: ARG002
        return (_employee(tenant_id),)

    def get_incident(self, incident_id: int):  # noqa: ARG002
        return None

    def get_effective_property(self, **_: object):
        return SimpleNamespace(id=1)

    def get_daily_status(self, **_: object):
        return SimpleNamespace(on_duty="ON_DUTY", current_load=0)

    def get_attendance(self, **_: object):
        return SimpleNamespace(check_in_at=utc_now())

    def list_employee_role_codes(self, user_id: int, **_: object):  # noqa: ARG002
        return frozenset({"STAFF"})

    def get_dispatch_history(self, **_: object):
        return (None, None, None)

    def list_employee_duties(self, employee_id: int):  # noqa: ARG002
        return self._duties

    def list_employee_skills(self, employee_id: int):  # noqa: ARG002
        return self._skills

    def count_same_building_history(self, **_: object) -> int:
        return 0

    def get_user_status(self, user_id: int):  # noqa: ARG002 - stub
        return "ACTIVE"


class _StubWorkOrderRepo:
    def get_property(self, property_id: int):  # noqa: ARG002
        return SimpleNamespace(timezone="Asia/Shanghai")

    def employee_global_occupancy(self, **_: object) -> int:
        return 0

    def employee_last_assignment_id(self, **_: object) -> int | None:
        return None


def _settings_stub():
    """Real weights, but no rejection cooldown so the history lookup is skipped."""

    return get_settings().model_copy(update={"dispatch_reject_cooldown_seconds": 0})


def _dispatch_service(*, duties: frozenset[str], skills: frozenset[str]) -> AutoDispatchService:
    service = object.__new__(AutoDispatchService)
    service._ai = _StubDispatchAi(duties=duties, skills=skills)
    service._work_orders = _StubWorkOrderRepo()
    service._settings = _settings_stub()
    return service


def test_auto_snapshot_carries_the_capability_note() -> None:
    service = _dispatch_service(
        duties=frozenset({DUTY}), skills=frozenset({PLUMBING_SKILL})
    )
    record = SimpleNamespace(
        id=1, tenant_id=1, property_id=1, house_id=1, incident_id=None
    )

    _, snapshot = service._evaluate_live_candidates(
        record,
        _mapped(required_duty=DUTY, required_skill=UNCONSTRAINED_SKILL),
    )

    assert len(snapshot) == 1
    row = snapshot[0]
    assert row["eligible"] is True
    assert row["filter_reason"] is None
    assert row["required_duty"] == DUTY
    assert row["required_skill"] == UNCONSTRAINED_SKILL
    assert row["capability_note"] == SKILL_UNCONSTRAINED_OTHER, (
        "an eligible row that only passed because `other` is unconstrained must say so"
    )


def test_auto_snapshot_has_no_note_for_a_concrete_skill() -> None:
    service = _dispatch_service(
        duties=frozenset({DUTY}), skills=frozenset({PLUMBING_SKILL})
    )
    record = SimpleNamespace(
        id=1, tenant_id=1, property_id=1, house_id=1, incident_id=None
    )

    _, snapshot = service._evaluate_live_candidates(
        record, _mapped(required_duty=DUTY, required_skill=PLUMBING_SKILL)
    )

    assert snapshot[0]["capability_note"] is None


# ------------------------------------------------------ F2 recommendation rows


class _StubRecommendationAi(_StubDispatchAi):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.rows: list[object] = []

    def delete_recommendations(self, analysis_id: int) -> None:  # noqa: ARG002
        return None

    def add_recommendation(self, row: object) -> None:
        self.rows.append(row)


def _analysis_service(*, duties: frozenset[str], skills: frozenset[str]) -> AnalysisService:
    service = object.__new__(AnalysisService)
    service._ai = _StubRecommendationAi(duties=duties, skills=skills)
    service._work_orders = _StubWorkOrderRepo()
    service._clock = utc_now
    service._settings = _settings_stub()
    return service


def test_recommendation_row_records_the_capability_note() -> None:
    service = _analysis_service(
        duties=frozenset({DUTY}), skills=frozenset({PLUMBING_SKILL})
    )
    record = SimpleNamespace(
        id=1, tenant_id=1, property_id=1, house_id=1, incident_id=None
    )
    analysis = SimpleNamespace(id=9)

    service._write_recommendations(
        record,
        analysis,
        _mapped(required_duty=DUTY, required_skill=UNCONSTRAINED_SKILL),
        None,
    )

    assert len(service._ai.rows) == 1  # type: ignore[attr-defined]
    row = service._ai.rows[0]  # type: ignore[attr-defined]
    assert row.eligible == 1
    assert SKILL_UNCONSTRAINED_OTHER in row.reason_codes
    assert row.filter_reason is None


def test_ineligible_recommendation_keeps_reason_and_note() -> None:
    service = _analysis_service(duties=frozenset({"CLEANING"}), skills=frozenset({"CLEANING"}))
    record = SimpleNamespace(
        id=1, tenant_id=1, property_id=1, house_id=1, incident_id=None
    )

    service._write_recommendations(
        record,
        SimpleNamespace(id=9),
        _mapped(required_duty=DUTY, required_skill=UNCONSTRAINED_SKILL),
        None,
    )

    row = service._ai.rows[0]  # type: ignore[attr-defined]
    assert row.eligible == 0
    assert row.filter_reason == FilterReason.DUTY_MISMATCH.value
    assert row.reason_codes[0] == FilterReason.DUTY_MISMATCH.value
    assert SKILL_UNCONSTRAINED_OTHER in row.reason_codes


def test_capability_note_is_only_produced_for_the_unconstrained_skill() -> None:
    assert capability_note(required_duty=DUTY, required_skill=UNCONSTRAINED_SKILL) == (
        SKILL_UNCONSTRAINED_OTHER
    )
    assert capability_note(required_duty=DUTY, required_skill=PLUMBING_SKILL) is None
    assert capability_note(required_duty=None, required_skill=UNCONSTRAINED_SKILL) is None


def test_application_error_log_carries_the_denial_reason(monkeypatch) -> None:
    """PA-127 F3: the 409 envelope always had details.reason; the log line now does too."""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.exception_handlers import install_exception_handlers
    from app.core.request_context import RequestContextMiddleware

    logged: list[dict[str, object]] = []

    def _capture(event: str, **fields: object) -> None:
        logged.append({"event": event, **fields})

    monkeypatch.setattr("app.core.exception_handlers.logger.warning", _capture)

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)

    @app.get("/denied")
    def denied() -> None:
        raise eligibility_denied(
            reason="DUTY_MISMATCH",
            required_duty=DUTY,
            required_skill=UNCONSTRAINED_SKILL,
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/denied")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "DUTY_MISMATCH"

    entry = next(item for item in logged if item.get("event") == "application_error")
    assert entry["error_reason"] == "DUTY_MISMATCH"
    assert entry["error_detail_keys"] == ["capability_note", "reason", "required_duty", "required_skill"]
