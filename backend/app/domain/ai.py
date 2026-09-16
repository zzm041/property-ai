"""Pure AI analysis validation, mapping, and explainable dispatch scoring."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.work_order import WorkOrderCategory, WorkOrderPriority, WorkOrderSeverity


class AnalysisStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STALE = "STALE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class AiRisk(StrEnum):
    NONE = "none"
    SAFETY = "safety"
    FLOODING = "flooding"
    ELECTRIC = "electric"
    GAS = "gas"
    OTHER = "other"


class ProviderType(StrEnum):
    LLM = "LLM"
    VLM = "VLM"
    ASR = "ASR"


class AiCallStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INVALID = "INVALID"
    STALE = "STALE"


class FilterReason(StrEnum):
    NOT_ACTIVE = "NOT_ACTIVE"
    PROPERTY_NOT_EFFECTIVE = "PROPERTY_NOT_EFFECTIVE"
    NOT_ON_DUTY = "NOT_ON_DUTY"
    DUTY_MISMATCH = "DUTY_MISMATCH"
    SKILL_MISMATCH = "SKILL_MISMATCH"
    CAPACITY_FULL = "CAPACITY_FULL"
    NO_STAFF_ROLE = "NO_STAFF_ROLE"


AI_ANALYSIS_READ = "ai_analysis:read"
AI_ANALYSIS_REVIEW = "ai_analysis:review"
DISPATCH_RECOMMENDATION_READ = "dispatch_recommendation:read"
INCIDENT_READ = "incident:read"
INCIDENT_MANAGE = "incident:manage"
ANNOUNCEMENT_READ = "announcement:read"
ANNOUNCEMENT_DRAFT = "announcement:draft"
ANNOUNCEMENT_PUBLISH = "announcement:publish"

WORK_ORDER_ANALYSIS_PROMPT_VERSION = "wo-analysis-v1"
INCIDENT_SUMMARY_PROMPT_VERSION = "incident-summary-v1"
ANNOUNCEMENT_DRAFT_PROMPT_VERSION = "announcement-draft-v1"

DEFAULT_CONFIDENCE_THRESHOLD = 0.70

_CATEGORY_ALIASES = {
    "water_supply": WorkOrderCategory.PLUMBING,
    "plumbing": WorkOrderCategory.PLUMBING,
    "electrical": WorkOrderCategory.ELECTRICAL,
    "hvac": WorkOrderCategory.HVAC,
    "cleaning": WorkOrderCategory.CLEANING,
    "security": WorkOrderCategory.SECURITY,
    "other": WorkOrderCategory.OTHER,
    **{item.value.lower(): item for item in WorkOrderCategory},
    **{item.value: item for item in WorkOrderCategory},
}

_PRIORITY_ALIASES = {
    "p1": WorkOrderPriority.URGENT,
    "p2": WorkOrderPriority.HIGH,
    "p3": WorkOrderPriority.MEDIUM,
    "p4": WorkOrderPriority.LOW,
    **{item.value.lower(): item for item in WorkOrderPriority},
    **{item.value: item for item in WorkOrderPriority},
}

_SEVERITY_ALIASES = {
    "high": WorkOrderSeverity.HIGH,
    "medium": WorkOrderSeverity.MEDIUM,
    "low": WorkOrderSeverity.LOW,
    **{item.value: item for item in WorkOrderSeverity},
}

_SKILL_ALIASES = {
    "plumber": "PLUMBING",
    "plumbing": "PLUMBING",
    "electrician": "ELECTRICAL",
    "electrical": "ELECTRICAL",
    "hvac": "HVAC",
    "cleaning": "CLEANING",
    "security": "SECURITY",
}

_DUTY_FOR_CATEGORY = {
    WorkOrderCategory.PLUMBING: "MAINTENANCE",
    WorkOrderCategory.ELECTRICAL: "MAINTENANCE",
    WorkOrderCategory.HVAC: "MAINTENANCE",
    WorkOrderCategory.CLEANING: "CLEANING",
    WorkOrderCategory.SECURITY: "SECURITY",
    WorkOrderCategory.OTHER: "MAINTENANCE",
}


class IncidentSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=255)
    affected_scope: str = Field(min_length=1, max_length=255)
    common_symptom: str = Field(min_length=1, max_length=255)
    suggested_handling_note: str = Field(min_length=1, max_length=255)


class AnnouncementDraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)


class WorkOrderAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str = Field(min_length=1, max_length=32)
    subcategory: str = Field(min_length=1, max_length=64)
    severity: str = Field(min_length=1, max_length=16)
    priority: str = Field(min_length=1, max_length=16)
    summary: str = Field(min_length=1, max_length=255)
    risk: str = Field(min_length=1, max_length=32)
    required_role: str = Field(min_length=1, max_length=32)
    required_skill: str = Field(min_length=1, max_length=32)
    confidence: float = Field(ge=0, le=1)

    @field_validator("summary", "subcategory", "category", "required_skill")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("control characters are not allowed")
        return value


@dataclass(frozen=True, slots=True)
class MappedAnalysis:
    category: WorkOrderCategory
    subcategory: str
    severity: WorkOrderSeverity
    priority: WorkOrderPriority
    summary: str
    risk: AiRisk
    required_role: str
    required_skill: str
    required_duty: str
    confidence: float
    low_confidence: bool


def map_analysis_output(
    output: WorkOrderAnalysisOutput,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> MappedAnalysis:
    category = _CATEGORY_ALIASES.get(output.category) or _CATEGORY_ALIASES.get(
        output.category.casefold()
    )
    if category is None:
        category = WorkOrderCategory.OTHER
    priority = _PRIORITY_ALIASES.get(output.priority) or _PRIORITY_ALIASES.get(
        output.priority.casefold()
    )
    if priority is None:
        raise ValueError("priority is not a supported value")
    severity = _SEVERITY_ALIASES.get(output.severity) or _SEVERITY_ALIASES.get(
        output.severity.casefold()
    )
    if severity is None:
        raise ValueError("severity is not a supported value")
    try:
        risk = AiRisk(output.risk.casefold())
    except ValueError as exc:
        raise ValueError("risk is not a supported value") from exc
    role = output.required_role.strip().upper()
    if role not in {"STAFF", "SUPERVISOR"}:
        raise ValueError("required_role is not a supported value")
    skill = _SKILL_ALIASES.get(output.required_skill.casefold(), output.required_skill.upper())
    low = output.confidence < threshold or category is WorkOrderCategory.OTHER
    return MappedAnalysis(
        category=category,
        subcategory=output.subcategory[:64],
        severity=severity,
        priority=priority,
        summary=output.summary[:255],
        risk=risk,
        required_role=role,
        required_skill=skill,
        required_duty=_DUTY_FOR_CATEGORY[category],
        confidence=output.confidence,
        low_confidence=low,
    )


@dataclass(frozen=True, slots=True)
class DispatchCandidate:
    employee_id: int
    duty_codes: frozenset[str]
    skill_codes: frozenset[str]
    current_load: int
    max_capacity: int
    on_duty: bool
    employee_active: bool
    property_effective: bool
    has_staff_role: bool
    same_building_history: int = 0
    primary_incident_handler: bool = False


def hard_filter_candidate(
    candidate: DispatchCandidate,
    *,
    required_skill: str | None,
    required_duty: str | None,
) -> FilterReason | None:
    if not candidate.employee_active:
        return FilterReason.NOT_ACTIVE
    if not candidate.property_effective:
        return FilterReason.PROPERTY_NOT_EFFECTIVE
    if not candidate.on_duty:
        return FilterReason.NOT_ON_DUTY
    if not candidate.has_staff_role:
        return FilterReason.NO_STAFF_ROLE
    if required_duty and required_duty not in candidate.duty_codes:
        return FilterReason.DUTY_MISMATCH
    if required_skill and required_skill not in candidate.skill_codes:
        return FilterReason.SKILL_MISMATCH
    if candidate.current_load >= candidate.max_capacity:
        return FilterReason.CAPACITY_FULL
    return None


def score_candidate(
    candidate: DispatchCandidate,
    *,
    required_skill: str | None,
    required_duty: str | None,
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    duty_score = 1.0 if required_duty and required_duty in candidate.duty_codes else 0.0
    skill_score = 1.0 if required_skill and required_skill in candidate.skill_codes else 0.0
    capacity = (
        (candidate.max_capacity - candidate.current_load) / candidate.max_capacity
        if candidate.max_capacity > 0
        else 0.0
    )
    building = 1.0 if candidate.same_building_history > 0 else 0.0
    primary = 1.0 if candidate.primary_incident_handler else 0.0
    score = (
        0.25 * duty_score
        + 0.20 * skill_score
        + 0.20 * capacity
        + 0.10 * building
        + 0.15 * primary
        + 0.10 * 0.5
    )
    if duty_score:
        reasons.append("DUTY_MATCH")
    if skill_score:
        reasons.append("SKILL_MATCH")
    reasons.append("LOAD_REMAINING")
    if building:
        reasons.append("SAME_BUILDING_HISTORY")
    if primary:
        reasons.append("PRIMARY_INCIDENT_HANDLER")
    return round(score, 4), tuple(reasons)


def rank_candidates(
    candidates: tuple[DispatchCandidate, ...],
    *,
    required_skill: str | None,
    required_duty: str | None,
) -> tuple[tuple[DispatchCandidate, float, tuple[str, ...]], ...]:
    eligible: list[tuple[DispatchCandidate, float, tuple[str, ...]]] = []
    for candidate in candidates:
        if hard_filter_candidate(
            candidate,
            required_skill=required_skill,
            required_duty=required_duty,
        ) is not None:
            continue
        score, reasons = score_candidate(
            candidate,
            required_skill=required_skill,
            required_duty=required_duty,
        )
        eligible.append((candidate, score, reasons))
    eligible.sort(
        key=lambda item: (
            -item[1],
            item[0].current_load,
            -item[0].same_building_history,
            item[0].employee_id,
        )
    )
    return tuple(eligible)
