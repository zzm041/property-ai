"""Staff attendance routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, EmployeeErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.employee import (
    AttendanceActionRequest,
    AttendanceRecordData,
    AttendanceSnapshotData,
    DailyStatusData,
    UpdateAttendanceStatusRequest,
)
from app.service.employee import EmployeeService

router = APIRouter(prefix="/staff", tags=["staff-attendance"])


def get_employee_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> EmployeeService:
    return EmployeeService(session)


def _attendance_data(record) -> AttendanceRecordData:
    return AttendanceRecordData(
        id=record.id,
        employee_id=record.employee_id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        business_date=record.business_date,
        check_in_at=record.check_in_at,
        check_out_at=record.check_out_at,
        source=record.source,
    )


def _daily_data(status) -> DailyStatusData:
    return DailyStatusData(
        id=status.id,
        employee_id=status.employee_id,
        tenant_id=status.tenant_id,
        property_id=status.property_id,
        business_date=status.business_date,
        on_duty=status.on_duty,
        leave_type=status.leave_type,
        current_load=status.current_load,
    )


def _reject_header_mismatch(*, property_id: int, x_property_id: str | None) -> None:
    if x_property_id is None or x_property_id == "":
        return
    try:
        declared = int(x_property_id)
    except ValueError as exc:
        raise AppError(
            code=EmployeeErrorCode.INVALID_INPUT,
            message="X-Property-Id is invalid",
            status_code=422,
        ) from exc
    if declared != property_id:
        raise AppError(
            code=EmployeeErrorCode.INVALID_INPUT,
            message="X-Property-Id does not match the target property",
            status_code=422,
        )


@router.post("/attendance/checkin")
def check_in(
    body: AttendanceActionRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    attendance, daily = service.check_in(
        actor=actor,
        property_id=body.property_id,
        request_id=request_id_for(request),
        claimed_tenant_id=body.tenant_id,
    )
    return success_envelope(
        request,
        AttendanceSnapshotData(
            business_date=attendance.business_date,
            attendance=_attendance_data(attendance),
            daily_status=_daily_data(daily),
        ).model_dump(mode="json"),
    )


@router.post("/attendance/checkout")
def check_out(
    body: AttendanceActionRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    attendance, daily = service.check_out(
        actor=actor,
        property_id=body.property_id,
        request_id=request_id_for(request),
        claimed_tenant_id=body.tenant_id,
    )
    return success_envelope(
        request,
        AttendanceSnapshotData(
            business_date=attendance.business_date,
            attendance=_attendance_data(attendance),
            daily_status=_daily_data(daily),
        ).model_dump(mode="json"),
    )


@router.patch("/attendance/status")
def update_attendance_status(
    body: UpdateAttendanceStatusRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    attendance, daily = service.update_my_status(
        actor=actor,
        property_id=body.property_id,
        on_duty=body.on_duty,
        request_id=request_id_for(request),
        leave_type=body.leave_type,
        claimed_tenant_id=body.tenant_id,
    )
    return success_envelope(
        request,
        AttendanceSnapshotData(
            business_date=attendance.business_date,
            attendance=_attendance_data(attendance),
            daily_status=_daily_data(daily),
        ).model_dump(mode="json"),
    )


@router.get("/attendance/today")
def get_today_status(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    property_id: Annotated[int, Query(gt=0)],
    tenant_id: Annotated[int | None, Query(gt=0)] = None,
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=property_id, x_property_id=x_property_id)
    attendance, daily, business_date = service.get_my_today_status(
        actor=actor,
        property_id=property_id,
        claimed_tenant_id=tenant_id,
    )
    return success_envelope(
        request,
        AttendanceSnapshotData(
            business_date=business_date,
            attendance=None if attendance is None else _attendance_data(attendance),
            daily_status=None if daily is None else _daily_data(daily),
        ).model_dump(mode="json"),
    )
