"""Staff WorkOrder accept, reject, start, feedback, receipt, and escalate."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies.auth import get_current_principal
from app.api.v1.work_orders import get_work_order_service, work_order_data
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import (
    RejectWorkOrderRequest,
    StaffEscalateRequest,
    StaffFeedbackRequest,
    StaffReceiptRequest,
    StaffVersionRequest,
    WorkOrderListData,
)
from app.service.work_order import WorkOrderService

router = APIRouter(prefix="/staff", tags=["staff-work-orders"])


@router.get("/work-orders")
def list_staff_work_orders(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_staff_work_orders(
        actor=actor,
        after_id=after_id,
        limit=limit,
    )
    items = [work_order_data(item, overdue=service.is_overdue(item)) for item in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        WorkOrderListData(items=items, next_cursor=next_cursor).model_dump(mode="json"),
    )


@router.post("/work-orders/{work_order_id}/accept")
def accept_work_order(
    work_order_id: int,
    body: StaffVersionRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.accept(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/reject")
def reject_work_order(
    work_order_id: int,
    body: RejectWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.reject(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        reason=body.reason,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/start")
def start_work_order(
    work_order_id: int,
    body: StaffVersionRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.start(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/feedback")
def feedback_work_order(
    work_order_id: int,
    body: StaffFeedbackRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.feedback(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        text=body.text,
        process_reason=body.process_reason,
        file_ids=body.file_ids,
        taken_phase=body.taken_phase,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/receipts")
def submit_receipt(
    work_order_id: int,
    body: StaffReceiptRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.submit_receipt(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        problem_type=body.problem_type,
        method=body.method,
        resolved=body.resolved,
        materials=body.materials,
        fee_amount=body.fee_amount,
        fee_flag=body.fee_flag,
        unresolved_reason=body.unresolved_reason,
        follow_up=body.follow_up,
        before_file_ids=body.before_file_ids,
        after_file_ids=body.after_file_ids,
        file_ids=body.file_ids,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/escalate")
def escalate_work_order(
    work_order_id: int,
    body: StaffEscalateRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.escalate(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        process_reason=body.process_reason,
        reason=body.reason,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))
