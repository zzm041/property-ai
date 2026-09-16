"""Supervisor WorkOrder queue, assign/reassign, and exception decisions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.api.v1.work_orders import get_work_order_service, work_order_data
from app.core.errors import AppError, WorkOrderErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import (
    AssignWorkOrderRequest,
    DecideWorkOrderRequest,
    WorkOrderListData,
)
from app.service.work_order import WorkOrderService

router = APIRouter(prefix="/supervisor", tags=["supervisor-work-orders"])


def _reject_header_mismatch(*, property_id: int, x_property_id: str | None) -> None:
    if x_property_id is None or x_property_id == "":
        return
    try:
        declared = int(x_property_id)
    except ValueError as exc:
        raise AppError(
            code=WorkOrderErrorCode.INVALID_INPUT,
            message="X-Property-Id is invalid",
            status_code=422,
        ) from exc
    if declared != property_id:
        raise AppError(
            code=WorkOrderErrorCode.SCOPE_MISMATCH,
            message="X-Property-Id does not match the target property",
            status_code=422,
        )


@router.get("/work-orders")
def list_supervisor_work_orders(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    property_id: Annotated[int, Query(gt=0)],
    tenant_id: Annotated[int | None, Query(gt=0)] = None,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(property_id=property_id, x_property_id=x_property_id)
    records = service.list_supervisor_queue(
        actor=actor,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
        claimed_tenant_id=tenant_id,
    )
    items = [work_order_data(item, overdue=service.is_overdue(item)) for item in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        WorkOrderListData(items=items, next_cursor=next_cursor).model_dump(mode="json"),
    )


@router.post("/work-orders/{work_order_id}/assign")
def assign_work_order(
    work_order_id: int,
    body: AssignWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    if body.property_id is not None:
        _reject_header_mismatch(
            property_id=body.property_id,
            x_property_id=x_property_id,
        )
    record = service.assign(
        actor=actor,
        work_order_id=work_order_id,
        employee_id=body.employee_id,
        request_id=request_id_for(request),
        reason=body.reason,
        version=body.version,
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/decide")
def decide_work_order(
    work_order_id: int,
    body: DecideWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.decide(
        actor=actor,
        work_order_id=work_order_id,
        decision=body.decision,
        request_id=request_id_for(request),
        employee_id=body.employee_id,
        reason=body.reason,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))
