"""Resident bind-request and house-relation API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.identity import ResidentBindStatus, ResidentRelationType


class CreateResidentBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    house_id: int = Field(gt=0)
    relation_type: ResidentRelationType
    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class RejectResidentBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class ResidentBindRequestData(BaseModel):
    id: int
    user_id: int
    tenant_id: int
    property_id: int
    house_id: int
    relation_type: str
    status: ResidentBindStatus | str
    reviewer_id: int | None
    review_reason: str | None
    requested_at: datetime
    reviewed_at: datetime | None


class ResidentBindReviewData(ResidentBindRequestData):
    applicant_display_name: str
    property_name: str
    building_name: str
    unit_name: str
    room_no: str


class ResidentHouseData(BaseModel):
    relation_id: int
    house_id: int
    tenant_id: int
    property_id: int
    room_no: str
    property_name: str = ""
    property_address: str = ""
    building_name: str = ""
    unit_name: str = ""
    relation_type: str
    status: str
    valid_from: datetime
    valid_to: datetime | None


class BindRequestListData(BaseModel):
    items: list[ResidentBindRequestData]
    next_cursor: int | None


class BindReviewListData(BaseModel):
    items: list[ResidentBindReviewData]
    next_cursor: int | None


class HouseRelationListData(BaseModel):
    items: list[ResidentHouseData]
    next_cursor: int | None
