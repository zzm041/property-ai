"""Employee and attendance API schemas."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateEmployeeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int | None = Field(default=None, gt=0)
    tenant_id: int = Field(gt=0)
    employee_no: str = Field(min_length=1, max_length=32)
    property_id: int = Field(gt=0)
    department_id: int | None = Field(default=None, gt=0)
    duty_ids: tuple[int, ...] = ()
    skill_ids: tuple[int, ...] = ()
    max_capacity: int = Field(default=1, ge=1, le=100)


class UpdateEmployeeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    department_id: int | None = Field(default=None, gt=0)
    max_capacity: int | None = Field(default=None, ge=1, le=100)
    add_duty_ids: tuple[int, ...] | None = None
    add_skill_ids: tuple[int, ...] | None = None


class CreateEmployeeCatalogItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(gt=0)
    property_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class IssueEmployeeInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=20)


class ClaimEmployeeInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=128)


class AssignEmployeePropertyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int = Field(gt=0)


class AttendanceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int = Field(gt=0)
    tenant_id: int | None = Field(default=None, gt=0)


class UpdateAttendanceStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int = Field(gt=0)
    on_duty: str = Field(min_length=1, max_length=32)
    leave_type: str | None = Field(default=None, max_length=32)
    tenant_id: int | None = Field(default=None, gt=0)


class EmployeeData(BaseModel):
    id: int
    user_id: int | None
    tenant_id: int
    employee_no: str
    department_id: int | None
    status: str
    max_capacity: int


class EmployeePropertyData(BaseModel):
    id: int
    employee_id: int
    tenant_id: int
    property_id: int
    status: str
    valid_from: datetime
    valid_to: datetime | None


class EmployeeCatalogItemData(BaseModel):
    id: int
    tenant_id: int
    code: str
    name: str
    status: str


class EmployeeCatalogData(BaseModel):
    duties: list[EmployeeCatalogItemData]
    skills: list[EmployeeCatalogItemData]


class EmployeeDetailData(BaseModel):
    employee: EmployeeData
    assignments: list[EmployeePropertyData]
    duties: list[EmployeeCatalogItemData] = Field(default_factory=list)
    skills: list[EmployeeCatalogItemData] = Field(default_factory=list)


class AttendanceRecordData(BaseModel):
    id: int
    employee_id: int
    tenant_id: int
    property_id: int
    business_date: date
    check_in_at: datetime
    check_out_at: datetime | None
    source: str


class DailyStatusData(BaseModel):
    id: int
    employee_id: int
    tenant_id: int
    property_id: int
    business_date: date
    on_duty: str
    leave_type: str | None
    current_load: int


class AttendanceSnapshotData(BaseModel):
    business_date: date
    attendance: AttendanceRecordData | None
    daily_status: DailyStatusData | None


class DailyStatusListData(BaseModel):
    business_date: date
    items: list[DailyStatusData]
    next_cursor: int | None
