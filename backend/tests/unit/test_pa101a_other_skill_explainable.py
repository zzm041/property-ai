"""PA-101-A follow-up: `other` on a concrete category must be explainable.

The deadlock itself was closed by treating `other` as an unconstrained skill
marker. What remained was that the decision was silent: the capability gate
answered `None` exactly like "there is no requirement at all", and the manual
dispatch 409 carried no reason, so an operator could not tell a skill gate from
attendance or capacity.

These tests pin the three dispatch cases that matter -- the employee holds the
category skill, the employee holds only `OTHER`, the employee holds neither --
plus the readable 409 reason and the counter-example that keeps a real skill
mismatch a hard rejection.
"""

from __future__ import annotations

import pytest

from app.domain.ai import (
    SKILL_UNCONSTRAINED_OTHER,
    FilterReason,
    capability_filter_reason,
    capability_note,
    eligibility_denial_details,
    hard_filter_candidate,
    DispatchCandidate,
)

# A water-supply order: duty MAINTENANCE, the model answered required_skill=other.
REQUIRED_DUTY = "MAINTENANCE"
REQUIRED_SKILL = "OTHER"


def _candidate(*, duty_codes: set[str], skill_codes: set[str]) -> DispatchCandidate:
    return DispatchCandidate(
        employee_id=1,
        duty_codes=frozenset(duty_codes),
        skill_codes=frozenset(skill_codes),
        current_load=0,
        max_capacity=4,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )


def test_employee_holding_the_category_skill_passes_the_gate() -> None:
    """员工持该类技能（PLUMBING）→ 放行。"""
    reason = capability_filter_reason(
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"PLUMBING"}),
        required_duty=REQUIRED_DUTY,
        required_skill=REQUIRED_SKILL,
    )
    assert reason is None
    candidate_reason = hard_filter_candidate(
        _candidate(duty_codes={"MAINTENANCE"}, skill_codes={"PLUMBING"}),
        required_skill=REQUIRED_SKILL,
        required_duty=REQUIRED_DUTY,
    )
    assert candidate_reason is None
    # The gate opened for a stated reason, not by accident.
    assert capability_note(required_duty=REQUIRED_DUTY, required_skill=REQUIRED_SKILL) == (
        SKILL_UNCONSTRAINED_OTHER
    )


def test_employee_holding_only_other_passes_the_gate() -> None:
    """员工只持 OTHER → 同样放行（`other` 不是员工技能码）。"""
    reason = hard_filter_candidate(
        _candidate(duty_codes={"MAINTENANCE"}, skill_codes={"OTHER"}),
        required_skill=REQUIRED_SKILL,
        required_duty=REQUIRED_DUTY,
    )
    assert reason is None


def test_employee_without_the_category_skill_is_rejected_with_a_reason() -> None:
    """员工无该类技能（无 PLUMBING/HVAC/ELECTRICAL）→ 仍被拦，且 409 可读。"""
    details = eligibility_denial_details(
        FilterReason.DUTY_MISMATCH.value,
        required_duty="CLEANING",
        required_skill="OTHER",
    )
    assert details["reason"] == "DUTY_MISMATCH"
    assert details["capability_note"] == SKILL_UNCONSTRAINED_OTHER
    # Duty mismatch is what stops a maintenance-only crew on a cleaning order.
    assert (
        hard_filter_candidate(
            _candidate(duty_codes={"MAINTENANCE"}, skill_codes={"PLUMBING"}),
            required_skill="OTHER",
            required_duty="CLEANING",
        )
        is FilterReason.DUTY_MISMATCH
    )


def test_concrete_skill_requirement_stays_a_hard_rejection() -> None:
    """反例：模型给出具体受限技能时，闸门不放松。"""
    reason = capability_filter_reason(
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"PLUMBING"}),
        required_duty=REQUIRED_DUTY,
        required_skill="SECURITY",
    )
    assert reason is FilterReason.SKILL_MISMATCH
    assert capability_note(required_duty=REQUIRED_DUTY, required_skill="SECURITY") is None
    details = eligibility_denial_details(
        FilterReason.SKILL_MISMATCH.value,
        required_duty=REQUIRED_DUTY,
        required_skill="SECURITY",
    )
    assert details == {
        "reason": "SKILL_MISMATCH",
        "required_duty": REQUIRED_DUTY,
        "required_skill": "SECURITY",
    }


def test_ordinary_maintenance_skill_is_not_annotated_as_other() -> None:
    """反例：模型给 maintenance（普通维修）时不产生 OTHER 注解。"""
    assert capability_note(required_duty=REQUIRED_DUTY, required_skill="MAINTENANCE") is None
    assert capability_note(required_duty=None, required_skill="OTHER") is None


@pytest.mark.parametrize("category_skill", ["PLUMBING", "ELECTRICAL", "HVAC"])
def test_water_electrical_hvac_all_open_with_other(category_skill: str) -> None:
    """水/电/暖 + other 三种品类在能力过滤阶段都给出一致判定。"""
    for category, duty in (("PLUMBING", "MAINTENANCE"), ("ELECTRICAL", "MAINTENANCE"), ("HVAC", "MAINTENANCE")):
        assert (
            capability_filter_reason(
                duty_codes=frozenset({duty}),
                skill_codes=frozenset({category_skill}),
                required_duty=duty,
                required_skill="OTHER",
            )
            is None
        )
    assert capability_note(required_duty="MAINTENANCE", required_skill="OTHER") == (
        SKILL_UNCONSTRAINED_OTHER
    )


# ---------------------------------------------------------------------------
# Service layer: the manual dispatch 409 must carry the reason (PA-101-A's
# "no recovery path" half). These drive `WorkOrderService._eligibility_error`
# with a stub of the collaborators it reads, so the assertion is about the
# error body an operator actually receives.
# ---------------------------------------------------------------------------

import types
from datetime import UTC, datetime

from app.domain.work_order import AttendanceOnDuty, EmployeeStatus
from app.service.work_order import WorkOrderService


def _stub_service(*, required_duty: str, required_skill: str, skill_codes: set[str]):
    analysis = types.SimpleNamespace(
        get_user_status=lambda user_id: "ACTIVE",
        list_employee_role_codes=lambda user_id, *, tenant_id, property_id: frozenset({"STAFF"}),
        get_attendance=lambda *, employee_id, property_id, business_date: types.SimpleNamespace(
            check_in_at=datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
        ),
        list_employee_duties=lambda employee_id: frozenset({"MAINTENANCE"}),
        list_employee_skills=lambda employee_id: frozenset(skill_codes),
    )
    employees = types.SimpleNamespace(
        find_effective_assignment=lambda **kwargs: object(),
        get_daily_status_for_update=lambda **kwargs: types.SimpleNamespace(
            on_duty=AttendanceOnDuty.ON_DUTY.value
        ),
    )
    repository = types.SimpleNamespace(
        get_property=lambda property_id: types.SimpleNamespace(timezone="Asia/Shanghai"),
        employee_global_occupancy=lambda **kwargs: 0,
    )
    return types.SimpleNamespace(
        _analysis=analysis,
        _employees=employees,
        _repository=repository,
        _clock=lambda: datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        _settings=types.SimpleNamespace(ai_confidence_threshold=0.7),
        _manual_requirements=lambda record: (required_duty, required_skill),
    )


def _record():
    return types.SimpleNamespace(
        id=1, tenant_id=5, property_id=5, category="water_supply", status="PENDING_ASSIGNMENT"
    )


def _employee(*, status: str = EmployeeStatus.ACTIVE.value, tenant_id: int = 5):
    return types.SimpleNamespace(
        id=1, tenant_id=tenant_id, user_id=1, status=status, max_capacity=4
    )


def test_service_allows_plumbing_crew_on_other_skill() -> None:
    service = _stub_service(required_duty="MAINTENANCE", required_skill="OTHER", skill_codes={"PLUMBING"})
    assert WorkOrderService._eligibility_error(service, _record(), _employee()) is None


def test_service_allows_other_skill_crew() -> None:
    service = _stub_service(required_duty="MAINTENANCE", required_skill="OTHER", skill_codes={"OTHER"})
    assert WorkOrderService._eligibility_error(service, _record(), _employee()) is None


def test_service_409_for_a_real_skill_gate_names_the_skill() -> None:
    """员工无该类技能 → 409 仍在，但带上 reason 与要求的技能。"""
    service = _stub_service(required_duty="MAINTENANCE", required_skill="SECURITY", skill_codes={"PLUMBING"})
    error = WorkOrderService._eligibility_error(service, _record(), _employee())
    assert error is not None
    assert error.status_code == 409
    assert error.details["reason"] == "SKILL_MISMATCH"
    assert error.details["required_skill"] == "SECURITY"
    assert "capability_note" not in error.details


def test_service_409_for_duty_mismatch_carries_the_other_note() -> None:
    service = _stub_service(required_duty="CLEANING", required_skill="OTHER", skill_codes={"PLUMBING"})
    error = WorkOrderService._eligibility_error(service, _record(), _employee())
    assert error is not None
    assert error.details["reason"] == "DUTY_MISMATCH"
    assert error.details["capability_note"] == SKILL_UNCONSTRAINED_OTHER


def test_service_409_for_attendance_is_distinguishable() -> None:
    """同一 409 码下，非技能原因（如停用员工）给不同 reason，便于自救。"""
    service = _stub_service(required_duty="MAINTENANCE", required_skill="OTHER", skill_codes={"PLUMBING"})
    error = WorkOrderService._eligibility_error(
        service, _record(), _employee(status=EmployeeStatus.DISABLED.value)
    )
    assert error is not None
    assert error.details["reason"] != "SKILL_MISMATCH"
    assert error.details["reason"] == "NOT_ACTIVE"
