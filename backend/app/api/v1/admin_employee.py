"""Admin employee identity and property-assignment routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.core.errors import AppError, EmployeeErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.employee import (
    AssignEmployeePropertyRequest,
    CreateEmployeeCatalogItemRequest,
    CreateEmployeeRequest,
    EmployeeCatalogData,
    EmployeeCatalogItemData,
    EmployeeData,
    EmployeeDetailData,
    EmployeePropertyData,
    IssueEmployeeInviteRequest,
    UpdateEmployeeRequest,
)
from app.service.employee import EmployeeService
from app.service.employee_identity import EmployeeIdentityService

router = APIRouter(prefix="/admin", tags=["admin-employees"])


def get_employee_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> EmployeeService:
    return EmployeeService(session)


def _reject_direct_user_id(*, user_id: int | None, settings: Settings) -> None:
    if user_id is not None and settings.app_env not in {"development", "test"}:
        raise AppError(
            code=EmployeeErrorCode.INVALID_INPUT,
            message="Direct user_id attach is not allowed",
            status_code=400,
        )


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


def _catalog_item_data(record) -> EmployeeCatalogItemData:
    return EmployeeCatalogItemData(
        id=record.id,
        tenant_id=record.tenant_id,
        code=record.code,
        name=record.name,
        status=record.status,
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
    settings: Annotated[Settings, Depends(get_settings)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=body.property_id, x_property_id=x_property_id)
    _reject_direct_user_id(user_id=body.user_id, settings=settings)
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
        add_duty_ids=body.add_duty_ids,
        add_skill_ids=body.add_skill_ids,
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
    duties, skills = service.get_employee_catalog_links(
        actor=actor,
        employee_id=employee_id,
    )
    return success_envelope(
        request,
        EmployeeDetailData(
            employee=_employee_data(employee),
            assignments=[_assignment_data(item) for item in assignments],
            duties=[_catalog_item_data(item) for item in duties],
            skills=[_catalog_item_data(item) for item in skills],
        ).model_dump(mode="json"),
    )


@router.get("/employee-catalog")
def list_employee_catalog(
    tenant_id: int,
    property_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    duties, skills = service.list_catalog(
        actor=actor,
        tenant_id=tenant_id,
        property_id=property_id,
    )
    return success_envelope(
        request,
        EmployeeCatalogData(
            duties=[_catalog_item_data(item) for item in duties],
            skills=[_catalog_item_data(item) for item in skills],
        ).model_dump(mode="json"),
    )


@router.post("/duties")
def create_duty(
    body: CreateEmployeeCatalogItemRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    record = service.create_duty(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _catalog_item_data(record).model_dump(mode="json"),
    )


@router.post("/skills")
def create_skill(
    body: CreateEmployeeCatalogItemRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EmployeeService, Depends(get_employee_service)],
) -> dict[str, object]:
    record = service.create_skill(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _catalog_item_data(record).model_dump(mode="json"),
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


@router.post("/employees/{employee_id}/invites")
def issue_employee_invite(
    employee_id: int,
    body: IssueEmployeeInviteRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_db_session)],
) -> dict[str, object]:
    token = EmployeeIdentityService(
        session, secret=settings.app_secret_key.get_secret_value()
    ).issue_invite(
        actor=actor,
        employee_id=employee_id,
        target_phone=body.phone,
        request_id=request_id_for(request),
    )
    return success_envelope(request, {"invite_token": token})
