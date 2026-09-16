"""Pure AI mapping, hard-filter, and explainable scoring tests."""

from pydantic import ValidationError
import pytest

from app.domain.ai import (
    DispatchCandidate,
    FilterReason,
    WorkOrderAnalysisOutput,
    hard_filter_candidate,
    map_analysis_output,
    rank_candidates,
)
from app.domain.announcement import AnnouncementStatus, can_transition_announcement
from app.domain.work_order import WorkOrderCategory, WorkOrderPriority
from app.provider.errors import ProviderTimeoutError
from app.provider.llm import DevelopmentLLMProvider
from app.provider.asr import DevelopmentASRProvider
from app.provider.vision import DevelopmentVLMProvider


def test_structured_output_maps_docs_aliases() -> None:
    output = WorkOrderAnalysisOutput(
        category="electrical",
        subcategory="outlet_spark",
        severity="high",
        priority="p2",
        summary="Kitchen electrical outlet sparking",
        risk="electric",
        required_role="STAFF",
        required_skill="electrician",
        confidence=0.86,
    )
    mapped = map_analysis_output(output)
    assert mapped.category is WorkOrderCategory.ELECTRICAL
    assert mapped.priority is WorkOrderPriority.HIGH
    assert mapped.required_skill == "ELECTRICAL"
    assert mapped.required_duty == "MAINTENANCE"
    assert mapped.low_confidence is False


def test_unknown_category_is_other_and_low_confidence() -> None:
    output = WorkOrderAnalysisOutput(
        category="unknown_thing",
        subcategory="x",
        severity="low",
        priority="p4",
        summary="Unclear",
        risk="none",
        required_role="STAFF",
        required_skill="other",
        confidence=0.99,
    )
    mapped = map_analysis_output(output)
    assert mapped.category is WorkOrderCategory.OTHER
    assert mapped.low_confidence is True


def test_control_characters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkOrderAnalysisOutput(
            category="electrical",
            subcategory="x",
            severity="high",
            priority="p2",
            summary="bad\nline",
            risk="electric",
            required_role="STAFF",
            required_skill="electrician",
            confidence=0.9,
        )


def test_hard_filter_and_deterministic_ranking() -> None:
    eligible = DispatchCandidate(
        employee_id=2,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    busy = DispatchCandidate(
        employee_id=1,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=1,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    off_duty = DispatchCandidate(
        employee_id=3,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=2,
        on_duty=False,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    assert hard_filter_candidate(
        off_duty, required_skill="ELECTRICAL", required_duty="MAINTENANCE"
    ) is FilterReason.NOT_ON_DUTY
    ranked = rank_candidates(
        (busy, eligible, off_duty),
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
    )
    assert [item[0].employee_id for item in ranked] == [2, 1]


def test_announcement_cannot_publish_from_draft() -> None:
    assert can_transition_announcement(
        current=AnnouncementStatus.DRAFT.value,
        target=AnnouncementStatus.PENDING_REVIEW.value,
    )
    assert not can_transition_announcement(
        current=AnnouncementStatus.DRAFT.value,
        target=AnnouncementStatus.PUBLISHED.value,
    )


def test_fake_providers_are_deterministic() -> None:
    assert DevelopmentLLMProvider("success").structured_completion(
        prompt="x",
        prompt_version="wo-analysis-v1",
    )["category"] == "electrical"
    with pytest.raises(ProviderTimeoutError):
        DevelopmentLLMProvider("timeout").structured_completion(
            prompt="x",
            prompt_version="wo-analysis-v1",
        )
    with pytest.raises(ProviderTimeoutError):
        DevelopmentVLMProvider("timeout").inspect_image(
            storage_key="k",
            mime_type="image/jpeg",
        )
    with pytest.raises(ProviderTimeoutError):
        DevelopmentASRProvider("timeout").transcribe(
            storage_key="k",
            mime_type="audio/mpeg",
        )
