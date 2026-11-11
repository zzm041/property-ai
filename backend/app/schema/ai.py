"""AI analysis, incident, and announcement API schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AnalyzeWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class CreateAnnouncementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=4000)
    incident_id: int | None = Field(default=None, gt=0)
    ai_draft: bool = False
    tenant_id: int | None = Field(default=None, gt=0)
    scope_type: str = Field(default="PROPERTY", max_length=32)
    building_id: int | None = Field(default=None, gt=0)
    unit_id: int | None = Field(default=None, gt=0)
    audience: str = Field(default="RESIDENT", max_length=32)


class UpdateAnnouncementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    scope_type: str = Field(default="PROPERTY", max_length=32)
    building_id: int | None = Field(default=None, gt=0)
    unit_id: int | None = Field(default=None, gt=0)
    audience: str = Field(default="RESIDENT", max_length=32)


class PublishAnnouncementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(default="APPROVE", max_length=16)
    reason: str | None = Field(default=None, max_length=255)


class WorkOrderAnalysisData(BaseModel):
    id: int
    work_order_id: int
    analysis_version: int
    status: str
    category: str | None
    subcategory: str | None
    severity: str | None
    priority: str | None
    summary: str | None
    risk: str | None
    required_role: str | None
    required_skill: str | None
    confidence: Decimal | None
    error_code: str | None


class DispatchRecommendationData(BaseModel):
    employee_id: int
    score: Decimal
    rank_no: int
    eligible: bool
    reason_codes: list[str]
    filter_reason: str | None = None


class DispatchRecommendationListData(BaseModel):
    items: list[DispatchRecommendationData]
    next_cursor: int | None = None


class IncidentData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    building_id: int | None
    status: str
    category: str
    subcategory: str | None
    summary: str | None
    ai_summary: str | None
    affected_scope: str | None
    primary_work_order_id: int | None
    primary_employee_id: int | None
    work_order_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime


class IncidentListData(BaseModel):
    items: list[IncidentData]
    next_cursor: int | None = None


class UpdateIncidentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=2, max_length=255)
    expected_updated_at: datetime


class AnnouncementData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    incident_id: int | None
    title: str
    content: str
    status: str
    ai_draft: bool
    published_at: datetime | None
    created_at: datetime


class AnnouncementScopeData(BaseModel):
    id: int
    scope_type: str
    building_id: int | None
    unit_id: int | None
    audience: str | None


class AnnouncementReviewData(BaseModel):
    id: int
    reviewer_user_id: int
    decision: str
    reason: str | None
    created_at: datetime


class AnnouncementHistoryData(BaseModel):
    id: int
    action: str
    actor_user_id: int | None
    details: dict[str, object]
    created_at: datetime


class AdminAnnouncementData(AnnouncementData):
    created_by_user_id: int
    reviewed_by_user_id: int | None
    published_by_user_id: int | None
    updated_at: datetime
    scopes: list[AnnouncementScopeData]
    reviews: list[AnnouncementReviewData]
    history: list[AnnouncementHistoryData]


class AnnouncementListData(BaseModel):
    items: list[AnnouncementData]
    next_cursor: int | None = None


class AdminAnnouncementListData(BaseModel):
    items: list[AdminAnnouncementData]
    next_cursor: int | None = None
