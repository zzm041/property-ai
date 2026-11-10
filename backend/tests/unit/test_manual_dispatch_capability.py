"""Manual capability checks reuse effective classification, never raw low confidence."""
from types import SimpleNamespace as NS

import pytest

from app.core.config import Settings
from test_work_order_live_eligibility import ctx


def setup(ctx, category="PLUMBING", skill=None, status="COMPLETED", confidence=0.9):
    ctx.record.category = category
    ctx.service._settings = Settings(_env_file=None)
    ctx.service._analysis.list_employee_duties.return_value = frozenset({"MAINTENANCE"})
    ctx.service._analysis.list_employee_skills.return_value = frozenset()
    ctx.service._analysis.get_current_analysis.return_value = None if skill is None else NS(
        status=status, category=category, subcategory="synthetic", severity="low", priority="p3",
        summary="synthetic", risk="none", required_role="STAFF", required_skill=skill, confidence=confidence,
    )


@pytest.mark.parametrize("category,duty,skill", [("PLUMBING", "MAINTENANCE", "PLUMBING"),
    ("ELECTRICAL", "MAINTENANCE", "ELECTRICAL"), ("HVAC", "MAINTENANCE", "HVAC"),
    ("CLEANING", "CLEANING", "CLEANING"), ("SECURITY", "SECURITY", "SECURITY")])
def test_manual_dispatch_requires_known_category_duty(ctx, category, duty, skill):
    setup(ctx, category, skill)
    ctx.service._analysis.list_employee_duties.return_value = frozenset({"OTHER_DUTY"})
    ctx.service._analysis.list_employee_skills.return_value = frozenset({skill})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None
    ctx.service._analysis.list_employee_duties.return_value = frozenset({duty})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


@pytest.mark.parametrize("category", ["PLUMBING", "ELECTRICAL", "HVAC"])
def test_ordinary_repair_does_not_require_exclusive_trade_skill(ctx, category):
    setup(ctx, category, category)
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


@pytest.mark.parametrize("ignore_capacity", [False, True])
def test_special_skill_is_required_even_when_capacity_is_ignored(ctx, ignore_capacity):
    setup(ctx, "ELECTRICAL", "HIGH_VOLTAGE")
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=ignore_capacity) is not None
    ctx.service._analysis.list_employee_skills.return_value = frozenset({"HIGH_VOLTAGE"})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=ignore_capacity) is None


@pytest.mark.parametrize("status,confidence", [("LOW_CONFIDENCE", 0.2), ("FAILED", 0.9), ("STALE", 0.9), ("COMPLETED", 0.2)])
def test_unaccepted_analysis_skill_does_not_become_hard_constraint(ctx, status, confidence):
    setup(ctx, "ELECTRICAL", "HIGH_VOLTAGE", status, confidence)
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_known_category_still_requires_duty_when_ai_failed(ctx):
    setup(ctx, "CLEANING", "HIGH_VOLTAGE", "FAILED")
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None


def test_stale_different_category_does_not_supply_special_skill(ctx):
    setup(ctx, "ELECTRICAL", "HIGH_VOLTAGE")
    ctx.service._analysis.get_current_analysis.return_value.category = "CLEANING"
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_unknown_without_reliable_analysis_keeps_existing_manual_fallback(ctx):
    setup(ctx, "OTHER")
    ctx.service._analysis.list_employee_duties.return_value = frozenset()
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None
