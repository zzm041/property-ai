"""SLA policy management request and response contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


SlaCategory = Literal[
    "PLUMBING",
    "ELECTRICAL",
    "HVAC",
    "CLEANING",
    "SECURITY",
    "OTHER",
]
SlaPriority = Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
SlaPolicyState = Literal["ACTIVE", "DISABLED"]


class PutSlaPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(strict=True, gt=0)
    property_id: int | None = Field(default=None, strict=True, gt=0)
    category: SlaCategory
    priority: SlaPriority
    respond_minutes: int = Field(strict=True, ge=1, le=2_147_483_647)
    complete_minutes: int = Field(strict=True, ge=1, le=2_147_483_647)
    accept_minutes: int | None = Field(default=None, strict=True, ge=1, le=2_147_483_647)
    warning_lead_minutes: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    escalation_minutes: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    change_reason: str | None = Field(default=None, min_length=2, max_length=255)
    status: SlaPolicyState = "ACTIVE"
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window_order(self) -> Self:
        if self.respond_minutes > self.complete_minutes:
            raise ValueError("respond_minutes must not exceed complete_minutes")
        if self.accept_minutes is not None and self.accept_minutes > self.respond_minutes:
            raise ValueError("accept_minutes must not exceed respond_minutes")
        return self


class SlaPolicyData(BaseModel):
    id: int
    tenant_id: int
    property_id: int | None
    scope_type: Literal["TENANT", "PROPERTY"]
    category: SlaCategory
    priority: SlaPriority
    respond_minutes: int
    complete_minutes: int
    status: SlaPolicyState
    updated_at: datetime
    version_no: int | None = None
    accept_minutes: int | None = None
    warning_lead_minutes: int | None = None
    escalation_minutes: int | None = None
    change_reason: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class EffectiveSlaPolicyData(BaseModel):
    category: SlaCategory
    priority: SlaPriority
    source: Literal["PROPERTY", "TENANT", "CODE_DEFAULT"]
    policy_id: int | None
    respond_minutes: int
    complete_minutes: int


class SlaPolicyListData(BaseModel):
    property_id: int
    versioning_available: bool = False
    items: list[SlaPolicyData]
    effective: list[EffectiveSlaPolicyData]
