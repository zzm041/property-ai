"""Admin employee identity and property-assignment routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, EmployeeErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.employee import (
    AssignEmployeePropertyRequest,
    CreateEmployeeRequest,
    EmployeeData,
    EmployeeDetailData,
    EmployeePropertyData,
    UpdateEmployeeRequest,
)
from app.service.employee import EmployeeService

router = APIRouter(prefix="/admin", tags=["admin-employees"])


def get_employee_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> EmployeeService:
    return EmployeeService(session)


def _employee_data(record) -> EmployeeData:
    return EmployeeData(
        id=record.id,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        employee_no=record.employee_no,
        department_id=record.department_id,
        status=record.status,
        max_capacity=record.max_capacity,
    )


def _assignment_data(record) -> EmployeePropertyData:
    return EmployeePropertyData(
        id=record.id,
        employee_id=record.employee_id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        status=record.status,
        valid_from=record.valid_from,
        valid_to=record.valid_to,
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


@router.post("/employees")
def create_employee(
    body: CreateEmployeeRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    record = service.create_employee(
        actor=actor,
        user_id=body.user_id,
        tenant_id=body.tenant_id,
        employee_no=body.employee_no,
        property_id=body.property_id,
        request_id=request_id_for(request),
        department_id=body.department_id,
        duty_ids=body.duty_ids,
        skill_ids=body.skill_ids,
        max_capacity=body.max_capacity,
    )
    return success_envelope(request, _employee_data(record).model_dump(mode="json"))


@router.patch("/employees/{employee_id}")
def update_employee(
    employee_id: int,
    body: UpdateEmployeeRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    record = service.update_employee(
        actor=actor,
        employee_id=employee_id,
        request_id=request_id_for(request),
        status=body.status,
        department_id=body.department_id,
        max_capacity=body.max_capacity,
    )
    return success_envelope(request, _employee_data(record).model_dump(mode="json"))


@router.get("/employees/{employee_id}")
def get_employee(
    employee_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    employee, assignments = service.get_employee(actor=actor, employee_id=employee_id)
    return success_envelope(
        request,
        EmployeeDetailData(
            employee=_employee_data(employee),
            assignments=[_assignment_data(item) for item in assignments],
        ).model_dump(mode="json"),
    )


@router.post("/employees/{employee_id}/property-assignments")
def assign_employee_property(
    employee_id: int,
    body: AssignEmployeePropertyRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    assignment = service.assign_property(
        actor=actor,
        employee_id=employee_id,
        property_id=body.property_id,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _assignment_data(assignment).model_dump(mode="json"),
    )


@router.post("/employees/{employee_id}/property-assignments/{assignment_id}/end")
def end_employee_property(
    employee_id: int,
    assignment_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    del employee_id
    assignment = service.end_assignment(
        actor=actor,
        assignment_id=assignment_id,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _assignment_data(assignment).model_dump(mode="json"),
    )
