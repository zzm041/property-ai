"""Internal responsible-round evidence; no resident identity or free text."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class EmployeeHistoryFeedback(BaseModel):
    id: int
    recorded_at: datetime
    confirmed: bool | None
    rating: int | None
    unresolved: bool


class EmployeeHistoryItem(BaseModel):
    process_id: int
    work_order_id: int
    cycle_no: int
    category: str
    current_order_status: str
    started_at: datetime
    ended_at: datetime | None
    receipt_id: int | None
    receipt_finished_at: datetime | None
    reported_resolved: bool | None
    handling_mode: Literal['SELF','ASSISTED','EXTERNAL'] | None = None
    reported_participant_employee_id: int | None = None
    declaration_log_id: int | None = None
    latest_feedback: EmployeeHistoryFeedback | None = None


class EmployeeHistoryData(BaseModel):
    employee_id: int
    tenant_id: int
    property_id: int
    items: list[EmployeeHistoryItem]
    next_cursor: int | None
