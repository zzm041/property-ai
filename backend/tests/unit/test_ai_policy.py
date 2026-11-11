"""Pure AI mapping, hard-filter, and explainable scoring tests."""

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from app.domain.ai import (
    WORK_ORDER_ANALYSIS_PROMPT_VERSION,
    DispatchCandidate,
    DispatchWeights,
    FilterReason,
    WorkOrderAnalysisOutput,
    hard_filter_candidate,
    map_analysis_output,
    rank_candidates,
)
from app.domain.governance import AnnouncementStatus, can_transition_announcement
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


def test_unknown_category_is_rejected_instead_of_silently_relabelled() -> None:
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
    with pytest.raises(ValueError, match="category is not a supported value"):
        map_analysis_output(output)


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
    # New ordinary maintenance policy queues busy people even below max capacity.
    assert [item[0].employee_id for item in ranked] == [2]


def test_hard_filter_rejects_full_capacity() -> None:
    full = DispatchCandidate(
        employee_id=4,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=2,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    assert (
        hard_filter_candidate(
            full, required_skill="ELECTRICAL", required_duty="MAINTENANCE"
        )
        is FilterReason.CAPACITY_FULL
    )


def test_incident_follow_reuses_primary_handler_at_capacity() -> None:
    follower = DispatchCandidate(
        employee_id=4,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=2,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
        primary_incident_handler=True,
        incident_follow=True,
    )

    assert (
        hard_filter_candidate(
            follower,
            required_skill="ELECTRICAL",
            required_duty="MAINTENANCE",
        )
        is None
    )


def test_hard_filter_requires_active_user_and_live_check_in() -> None:
    candidate = DispatchCandidate(
        employee_id=5,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
        user_active=False,
    )
    assert hard_filter_candidate(
        candidate,
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
    ) is FilterReason.USER_NOT_ACTIVE
    checked_out = DispatchCandidate(
        employee_id=6,
        duty_codes=candidate.duty_codes,
        skill_codes=candidate.skill_codes,
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
        checked_in=False,
    )
    assert hard_filter_candidate(
        checked_out,
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
    ) is FilterReason.NOT_CHECKED_IN


def test_scoring_uses_configurable_operational_history_weights() -> None:
    proven = DispatchCandidate(
        employee_id=7,
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
        average_response_seconds=30,
        completion_rate=1.0,
        average_rating=5.0,
    )
    unknown = DispatchCandidate(
        employee_id=8,
        duty_codes=proven.duty_codes,
        skill_codes=proven.skill_codes,
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    weights = DispatchWeights(
        duty=0,
        skill=0,
        capacity=0,
        same_building=0,
        response=0.4,
        completion=0.3,
        rating=0.3,
    )
    ranked = rank_candidates(
        (unknown, proven),
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
        weights=weights,
    )
    assert [item[0].employee_id for item in ranked] == [7, 8]


def test_incident_primary_precedes_score_and_recent_checkin_breaks_ties() -> None:
    base = dict(
        duty_codes=frozenset({"MAINTENANCE"}),
        skill_codes=frozenset({"ELECTRICAL"}),
        current_load=0,
        max_capacity=2,
        on_duty=True,
        employee_active=True,
        property_effective=True,
        has_staff_role=True,
    )
    primary = DispatchCandidate(
        employee_id=9,
        check_in_at=datetime(2026, 9, 19, 1, tzinfo=UTC),
        primary_incident_handler=True,
        **base,
    )
    recent = DispatchCandidate(
        employee_id=10,
        check_in_at=datetime(2026, 9, 19, 3, tzinfo=UTC),
        **base,
    )
    older = DispatchCandidate(
        employee_id=11,
        check_in_at=datetime(2026, 9, 19, 2, tzinfo=UTC),
        **base,
    )
    ranked = rank_candidates(
        (older, recent, primary),
        required_skill="ELECTRICAL",
        required_duty="MAINTENANCE",
    )
    assert [item[0].employee_id for item in ranked] == [9, 10, 11]


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
        prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION,
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
