"""Scoped operations statistics response contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CountData(BaseModel):
    code: str
    count: int = Field(ge=0)


class WorkOrderStatsData(BaseModel):
    total: int = Field(ge=0)
    open: int = Field(ge=0)
    terminal: int = Field(ge=0)
    by_status: list[CountData]
    by_category: list[CountData]
    by_priority: list[CountData]


class IncidentStatsData(BaseModel):
    total: int = Field(ge=0)
    active: int = Field(ge=0)
    by_status: list[CountData]


class SlaClockStatsData(BaseModel):
    warning: int = Field(ge=0)
    overdue: int = Field(ge=0)
    by_state: list[CountData]


class StaffingStatsData(BaseModel):
    active_property_assignments: int = Field(ge=0)
    current_status_records: int = Field(ge=0)
    unreported_status: int = Field(ge=0)
    on_duty: int = Field(ge=0)
    available: int = Field(ge=0)
    current_load: int = Field(ge=0)
    on_duty_capacity: int = Field(ge=0)


class PropertyOperationsData(BaseModel):
    property_id: int
    property_name: str
    timezone: str
    business_date: date
    work_orders_total: int = Field(ge=0)
    work_orders_open: int = Field(ge=0)
    work_orders_terminal: int = Field(ge=0)
    incidents_active: int = Field(ge=0)
    sla_warning: int = Field(ge=0)
    sla_overdue: int = Field(ge=0)
    active_staff_assignments: int = Field(ge=0)
    unreported_status: int = Field(ge=0)
    on_duty: int = Field(ge=0)
    available: int = Field(ge=0)


class OperationsOverviewData(BaseModel):
    scope_type: Literal["TENANT", "PROPERTY"]
    tenant_id: int
    property_id: int | None
    generated_at: datetime
    property_count: int = Field(ge=0)
    work_orders: WorkOrderStatsData
    incidents: IncidentStatsData
    sla_clocks: SlaClockStatsData
    staffing: StaffingStatsData
    properties: list[PropertyOperationsData]
