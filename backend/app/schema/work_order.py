"""WorkOrder API request/response schemas. extra=forbid on writes."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    house_id: int = Field(gt=0)
    text: str | None = Field(default=None, max_length=4000)
    file_ids: tuple[int, ...] = ()
    category: str | None = Field(default=None, max_length=32)
    priority: str | None = Field(default=None, max_length=16)
    severity: str | None = Field(default=None, max_length=16)
    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class AppendWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=4000)
    file_ids: tuple[int, ...] = ()
    taken_phase: str | None = Field(default=None, max_length=16)


class CancelWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=255)
    version: int | None = Field(default=None, ge=1)


class ReopenWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=255)
    version: int | None = Field(default=None, ge=1)


class ConfirmWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)


class RateWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


class AssignWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=255)
    version: int | None = Field(default=None, ge=1)
    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class DecideWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=32)
    employee_id: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=255)
    version: int | None = Field(default=None, ge=1)


class RejectWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=255)
    version: int | None = Field(default=None, ge=1)


class StaffVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)


class StaffFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=4000)
    process_reason: str | None = Field(default=None, max_length=32)
    file_ids: tuple[int, ...] = ()
    taken_phase: str | None = Field(default=None, max_length=16)
    version: int | None = Field(default=None, ge=1)


class StaffReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_type: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=255)
    materials: str | None = Field(default=None, max_length=255)
    fee_amount: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )
    fee_flag: Literal["NONE", "RECORDED"] = "NONE"
    resolved: bool
    unresolved_reason: str | None = Field(default=None, max_length=255)
    follow_up: str | None = Field(default=None, max_length=255)
    before_file_ids: tuple[int, ...] = ()
    after_file_ids: tuple[int, ...] = ()
    # Backward-compatible alias for clients that submitted completion media
    # before the receipt contract exposed explicit before/after collections.
    file_ids: tuple[int, ...] = ()
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_fee_record(self) -> Self:
        if self.fee_flag == "RECORDED" and self.fee_amount is None:
            raise ValueError("fee_amount is required when fee_flag is RECORDED")
        if self.fee_flag == "NONE" and self.fee_amount is not None:
            raise ValueError("fee_amount must be omitted when fee_flag is NONE")
        return self


class StaffEscalateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_reason: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=255)
    version: int | None = Field(default=None, ge=1)


class RegisterFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0)
    original_name: str | None = Field(default=None, max_length=255)
    tenant_id: int | None = Field(default=None, gt=0)
    property_id: int | None = Field(default=None, gt=0)


class FileObjectData(BaseModel):
    id: int
    tenant_id: int
    property_id: int | None
    uploader_user_id: int
    mime_type: str
    size_bytes: int
    storage_key: str
    scanner_status: str
    created_at: datetime
    upload_url: str | None = None
    upload_expires_in: int | None = None


class WorkOrderMediaData(BaseModel):
    id: int
    file_id: int
    kind: str
    taken_phase: str
    uploader_user_id: int


class WorkOrderLogData(BaseModel):
    id: int
    actor_user_id: int | None
    actor_type: str
    from_status: str | None
    to_status: str
    process_reason: str
    event_code: str
    created_at: datetime


class WorkOrderAssignmentData(BaseModel):
    id: int
    employee_id: int
    # Automatic dispatch has no human actor. Migration 0009 permits NULL for
    # this column while manual assignments continue to return the actor id.
    assigned_by_user_id: int | None
    status: str
    assigned_at: datetime
    accept_at: datetime | None
    ended_at: datetime | None
    reason: str | None


class WorkOrderReceiptData(BaseModel):
    id: int
    process_id: int
    problem_type: str
    method: str
    materials: str | None
    fee_amount: Decimal | None
    fee_flag: str
    resolved: bool
    unresolved_reason: str | None
    follow_up: str | None
    finished_at: datetime


class WorkOrderData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    house_id: int
    reporter_user_id: int
    status: str
    process_reason: str
    category: str
    severity: str
    priority: str
    version: int
    current_assignment_id: int | None
    incident_id: int | None
    sla_due_at: datetime | None
    assigned_at: datetime | None
    accepted_at: datetime | None
    started_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sla_overdue: bool = False


class WorkOrderDetailData(BaseModel):
    work_order: WorkOrderData
    text: str | None = None
    asr_text: str | None = None
    extra_notes: str | None = None
    media: list[WorkOrderMediaData] = Field(default_factory=list)
    assignments: list[WorkOrderAssignmentData] = Field(default_factory=list)
    receipts: list[WorkOrderReceiptData] = Field(default_factory=list)
    logs: list[WorkOrderLogData] = Field(default_factory=list)


class WorkOrderListData(BaseModel):
    items: list[WorkOrderData]
    next_cursor: int | None = None
