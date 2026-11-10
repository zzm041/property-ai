"""Analysis domain data: enums, pydantic payloads, dataclasses and type aliases."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.work_order import (
    WorkOrderCategory,
    WorkOrderPriority,
    WorkOrderSeverity,
)


EVENT_CONTRACT = "observed-event-v2-source-id"


Label = Literal["滴渗水", "不出水", "不能供电", "火花烧损", "冒烟", "不制冷", "噪声", "杂物清扫",
                "敲门按铃", "疑似燃气气味", "烧焦变色", "焦煳气味", "松动", "水渍", "表面剥落",
                "外观无明显异常", "现象待核实", "照明不亮", "操作阻力", "进水扩散",
                "闭合失效", "排水缓慢", "部件下垂", "人员行为观察", "范围外请求"]


Subject = Literal["水龙头", "排水接头", "天花墙面", "插座", "照明", "空调", "扶手",
                  "楼道地面", "房门", "阀门管道", "窗户", "柜门", "排水区域", "人员", "未确定"]


Category = Literal["water_supply", "electrical", "hvac", "cleaning", "security", "other"]


Severity = Literal["low", "medium", "high"]


Priority = Literal["p1", "p2", "p3", "p4"]


Risk = Literal["none", "safety", "flooding", "electric", "gas", "other"]


RequiredRole = Literal["STAFF"]


RequiredSkill = Literal["maintenance", "cleaning", "security", "other"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: Label
    subject: Subject = Field(description="必须由evidence_id对应原文支持；原文未出现枚举主体时填未确定")
    evidence_id: str = Field(
        pattern=r"^(?:report|asr|vision):[1-9][0-9]*$",
        description="只能引用INPUT_DATA.evidence_catalog中由服务端生成的当前证据ID",
    )


class ObservedAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Category
    observations: list[Observation] = Field(min_length=1, max_length=6)
    severity: Severity
    priority: Priority
    summary: str = Field(min_length=1, max_length=255)
    risk: Risk
    required_role: RequiredRole
    required_skill: RequiredSkill
    confidence: float = Field(ge=0, le=1)


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
    USER_NOT_ACTIVE = "USER_NOT_ACTIVE"
    PROPERTY_NOT_EFFECTIVE = "PROPERTY_NOT_EFFECTIVE"
    NOT_CHECKED_IN = "NOT_CHECKED_IN"
    NOT_ON_DUTY = "NOT_ON_DUTY"
    DUTY_MISMATCH = "DUTY_MISMATCH"
    SKILL_MISMATCH = "SKILL_MISMATCH"
    CAPACITY_FULL = "CAPACITY_FULL"
    NO_STAFF_ROLE = "NO_STAFF_ROLE"
    RECENTLY_REJECTED = "RECENTLY_REJECTED"
    BUSY = "BUSY"


DISPATCH_CONTRACT_VERSION = "category-duty-skill-v1"


SKILL_RESOLUTION_ALIASED = "aliased"


SKILL_RESOLUTION_CANONICAL = "canonical"


SKILL_RESOLUTION_UNMAPPED = "unmapped"


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
    raw_required_skill: str | None = None
    skill_mapping_applied: bool = False
    skill_resolution: str = SKILL_RESOLUTION_CANONICAL
    dispatch_contract_version: str = DISPATCH_CONTRACT_VERSION
    event_trace: dict | None = None
    raw_category: str | None = None
    dispatch_fields_applied: bool = True


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
    user_active: bool = True
    checked_in: bool = True
    check_in_at: datetime | None = None
    same_building_history: int = 0
    primary_incident_handler: bool = False
    incident_follow: bool = False
    average_response_seconds: float | None = None
    completion_rate: float | None = None
    average_rating: float | None = None
    last_assignment_id: int = 0


@dataclass(frozen=True, slots=True)
class DispatchWeights:
    duty: float = 0.25
    skill: float = 0.20
    capacity: float = 0.20
    same_building: float = 0.10
    response: float = 0.10
    completion: float = 0.08
    rating: float = 0.07
    response_reference_seconds: float = 900.0

    @property
    def total(self) -> float:
        return (
            self.duty
            + self.skill
            + self.capacity
            + self.same_building
            + self.response
            + self.completion
            + self.rating
        )
