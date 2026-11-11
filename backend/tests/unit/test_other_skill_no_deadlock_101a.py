"""PA-101-A: `required_skill="other"` must not deadlock the whole order.

Before the fix every candidate failed with SKILL_MISMATCH (and manual dispatch
answered EMPLOYEE_NOT_ELIGIBLE/409 with no recovery path), because `other` is
the AI contract's "trade undetermined" marker rather than an employee skill.
The exemption is limited to that marker: restricted/custom skills stay hard
constraints, and the observation-binding negatives from PA-001 keep rejecting.
"""
from types import SimpleNamespace as NS

import pytest

from app.core.config import Settings
from app.domain.ai import FilterReason, UNCONSTRAINED_SKILLS, capability_filter_reason
from app.domain.ai import map_analysis_output
from app.domain.ai import WorkOrderAnalysisOutput
from test_work_order_live_eligibility import ctx  # noqa: F401


def setup(ctx, category, skill, confidence=0.9):
    ctx.record.category = category
    ctx.service._settings = Settings(_env_file=None)
    ctx.service._analysis.list_employee_duties.return_value = frozenset({"MAINTENANCE"})
    ctx.service._analysis.list_employee_skills.return_value = frozenset({"PLUMBING"})
    ctx.service._analysis.get_current_analysis.return_value = NS(
        status="COMPLETED", category=category, subcategory="synthetic", severity="low",
        priority="p3", summary="synthetic", risk="none", required_role="STAFF",
        required_skill=skill, confidence=confidence,
    )


@pytest.mark.parametrize("category", ["PLUMBING", "ELECTRICAL", "HVAC"])
def test_other_skill_no_longer_blocks_manual_dispatch(ctx, category):
    setup(ctx, category, "other")
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_other_skill_does_not_block_cleaning_dispatch(ctx):
    setup(ctx, "CLEANING", "other")
    ctx.service._analysis.list_employee_duties.return_value = frozenset({"CLEANING"})
    ctx.service._analysis.list_employee_skills.return_value = frozenset({"CLEANING"})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_duty_mismatch_is_still_enforced_with_other_skill(ctx):
    setup(ctx, "CLEANING", "other")
    ctx.service._analysis.list_employee_duties.return_value = frozenset({"MAINTENANCE"})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None


@pytest.mark.parametrize("skill", ["HIGH_VOLTAGE", "GAS_SAFETY"])
def test_restricted_skill_is_still_a_hard_constraint(ctx, skill):
    setup(ctx, "ELECTRICAL", skill)
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None
    ctx.service._analysis.list_employee_skills.return_value = frozenset({skill})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None


def test_other_is_the_only_unconstrained_marker():
    assert UNCONSTRAINED_SKILLS == frozenset({"OTHER"})
    assert capability_filter_reason(
        duty_codes=frozenset({"MAINTENANCE"}), skill_codes=frozenset(),
        required_duty="MAINTENANCE", required_skill="OTHER",
    ) is None
    assert capability_filter_reason(
        duty_codes=frozenset({"MAINTENANCE"}), skill_codes=frozenset(),
        required_duty="MAINTENANCE", required_skill="WELDING",
    ) is FilterReason.SKILL_MISMATCH


def test_stored_analysis_keeps_raw_other_marker():
    output = WorkOrderAnalysisOutput.model_validate({
        "category": "plumbing", "subcategory": "water_supply", "severity": "medium",
        "priority": "p2", "summary": "synthetic", "risk": "none", "required_role": "STAFF",
        "required_skill": "other", "confidence": 0.9,
    })
    assert output.required_skill == "other"
    mapped = map_analysis_output(output, threshold=0.6)
    assert mapped.required_skill == "OTHER"
    assert mapped.low_confidence is False


@pytest.mark.parametrize(
    "text,label,subject",
    [("客厅插座没有冒烟。", "冒烟", "插座"), ("水龙头没有滴水。", "滴渗水", "水龙头")],
)
def test_pa001_negatives_still_rejected(text, label, subject):
    from app.domain.ai import (
        ObservedAnalysisOutput,
        bind_observations,
        build_evidence_catalog,
    )

    payload = ObservedAnalysisOutput.model_validate({
        "category": "water_supply",
        "observations": [{"label": label, "subject": subject, "evidence_id": "report:1"}],
        "severity": "low", "priority": "p3", "summary": "synthetic", "risk": "none",
        "required_role": "STAFF", "required_skill": "other", "confidence": 0.9,
    })
    catalog = build_evidence_catalog(report_text=text, asr_text=None, vision_text=None)
    with pytest.raises(ValueError) as caught:
        bind_observations(payload, catalog)
    assert getattr(caught.value, "reason_code", None) == "prefixed_negative"
