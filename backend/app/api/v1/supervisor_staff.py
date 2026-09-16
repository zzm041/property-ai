"""Supervisor daily staff status routes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, EmployeeErrorCode
from app.domain.iam import Principal
from app.schema.common import success_envelope
from app.schema.employee import DailyStatusData, DailyStatusListData
from app.service.employee import EmployeeService

router = APIRouter(prefix="/supervisor", tags=["supervisor-staff"])


def get_employee_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> EmployeeService:
    return EmployeeService(session)


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


@router.get("/staff/daily")
def list_daily_staff(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int, Query(gt=0)],
    business_date: date | None = None,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=property_id, x_property_id=x_property_id)
    rows, resolved_date = service.list_property_daily_status(
        actor=actor,
        tenant_id=tenant_id,
        property_id=property_id,
        business_date=business_date,
        after_id=after_id,
        limit=limit,
    )
    items = [
        DailyStatusData(
            id=row.id,
            employee_id=row.employee_id,
            tenant_id=row.tenant_id,
            property_id=row.property_id,
            business_date=row.business_date,
            on_duty=row.on_duty,
            leave_type=row.leave_type,
            current_load=row.current_load,
        )
        for row in rows
    ]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        DailyStatusListData(
            business_date=resolved_date,
            items=items,
            next_cursor=next_cursor,
        ).model_dump(mode="json"),
    )
