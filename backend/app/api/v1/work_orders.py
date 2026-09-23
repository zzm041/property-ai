"""Resident WorkOrder create, read, append, cancel, confirm, reopen, and rate."""

from typing import Annotated
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.core.errors import AppError, WorkOrderErrorCode
from app.core.time import validate_timezone
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.work_order import (
    AppendWorkOrderRequest,
    CancelWorkOrderRequest,
    ConfirmWorkOrderRequest,
    CreateWorkOrderRequest,
    RateWorkOrderRequest,
    ReopenWorkOrderRequest,
    WorkOrderAssignmentData,
    WorkOrderData,
    WorkOrderDetailData,
    WorkOrderDisplayData,
    WorkOrderFeedbackData,
    WorkOrderListData,
    WorkOrderLogData,
    WorkOrderMediaData,
    WorkOrderProcessData,
    WorkOrderReceiptData,
)
from app.service.work_order import WorkOrderService

router = APIRouter(tags=["work-orders"])


def get_work_order_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkOrderService:
    return WorkOrderService(session, settings=settings)


def work_order_data(record, *, overdue: bool = False) -> WorkOrderData:
    return WorkOrderData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        house_id=record.house_id,
        reporter_user_id=record.reporter_user_id,
        status=record.status,
        process_reason=record.process_reason,
        category=record.category,
        severity=record.severity,
        priority=record.priority,
        version=record.version,
        current_assignment_id=record.current_assignment_id,
        incident_id=getattr(record, "incident_id", None),
        sla_due_at=record.sla_due_at,
        assigned_at=record.assigned_at,
        accepted_at=record.accepted_at,
        started_at=record.started_at,
        resolved_at=record.resolved_at,
        closed_at=record.closed_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        sla_overdue=overdue,
    )


def _require_idempotency_key(idempotency_key: str | None) -> str:
    if idempotency_key is None or not idempotency_key.strip():
        raise AppError(
            code=WorkOrderErrorCode.INVALID_INPUT,
            message="Idempotency-Key is required",
            status_code=422,
        )
    return idempotency_key.strip()


def work_order_display_data(
    detail: WorkOrderDetailData, context: dict[str, object]
) -> WorkOrderDisplayData:
    """Add labels without replacing raw UTC fields or using the device timezone."""
    timezone_name = context.get("property_timezone") or "UTC"
    try:
        timezone = validate_timezone(timezone_name)
    except (TypeError, ValueError):
        timezone_name, timezone = "UTC", UTC
    labels: dict[str, str] = {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_at") and isinstance(item, str):
                    instant = datetime.fromisoformat(item.replace("Z", "+00:00"))
                    # Schema-validated persisted timestamps are aware UTC.
                    if instant.tzinfo is not None:
                        local = instant.astimezone(timezone)
                        offset = local.strftime("%z")
                        labels[item] = local.strftime("%Y-%m-%d %H:%M:%S") + f" UTC{offset[:3]}:{offset[3:]}"
                elif isinstance(item, (dict, list)):
                    visit(item)

    visit(detail.model_dump(mode="json", exclude={"display"}))
    return WorkOrderDisplayData(
        property_timezone=timezone_name,
        assignee_names=context.get("assignee_names") or {},
        timestamp_labels=labels,
    )


@router.post("/work-orders")
def create_work_order(
    body: CreateWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    record = service.create_work_order(
        actor=actor,
        house_id=body.house_id,
        request_id=request_id_for(request),
        idempotency_key=_require_idempotency_key(idempotency_key),
        text=body.text,
        file_ids=body.file_ids,
        category=body.category,
        priority=body.priority,
        severity=body.severity,
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    return success_envelope(
        request,
        work_order_data(record, overdue=service.is_overdue(record)).model_dump(
            mode="json"
        ),
    )


@router.get("/work-orders")
def list_my_work_orders(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_my_work_orders(
        actor=actor,
        after_id=after_id,
        limit=limit,
    )
    page_records = records[:limit]
    items = [
        work_order_data(item, overdue=service.is_overdue(item))
        for item in page_records
    ]
    next_cursor = items[-1].id if len(records) > limit else None
    return success_envelope(
        request,
        WorkOrderListData(items=items, next_cursor=next_cursor).model_dump(mode="json"),
    )


@router.get("/work-orders/{work_order_id}")
def get_work_order(
    work_order_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    (
        record,
        content,
        media,
        assignments,
        processes,
        receipts,
        feedbacks,
        logs,
        display_context,
    ) = service.get_work_order_detail(actor=actor, work_order_id=work_order_id)
    process_by_id = {item.id: item for item in processes}
    detail = WorkOrderDetailData(
        work_order=work_order_data(record, overdue=service.is_overdue(record)),
        text=None if content is None else content.text,
        asr_text=None if content is None else content.asr_text,
        extra_notes=None if content is None else content.extra_notes,
        media=[
            WorkOrderMediaData(
                id=item.id,
                file_id=item.file_id,
                kind=item.kind,
                taken_phase=item.taken_phase,
                uploader_user_id=item.uploader_user_id,
                created_at=item.created_at,
            )
            for item in media
        ],
        assignments=[
            WorkOrderAssignmentData(
                id=item.id,
                employee_id=item.employee_id,
                assigned_by_user_id=item.assigned_by_user_id,
                status=item.status,
                assigned_at=item.assigned_at,
                accept_at=item.accept_at,
                ended_at=item.ended_at,
                reason=item.reason,
            )
            for item in assignments
        ],
        processes=[
            WorkOrderProcessData(
                id=item.id,
                cycle_no=item.cycle_no,
                assignee_id=item.assignee_id,
                started_at=item.started_at,
                ended_at=item.ended_at,
            )
            for item in processes
        ],
        receipts=[
            WorkOrderReceiptData(
                id=item.id,
                process_id=item.process_id,
                cycle_no=process_by_id[item.process_id].cycle_no,
                assignee_id=process_by_id[item.process_id].assignee_id,
                problem_type=item.problem_type,
                method=item.method,
                materials=item.materials,
                fee_amount=item.fee_amount,
                fee_flag=item.fee_flag,
                resolved=bool(item.resolved),
                unresolved_reason=item.unresolved_reason,
                follow_up=item.follow_up,
                finished_at=item.finished_at,
            )
            for item in receipts
        ],
        feedbacks=[
            WorkOrderFeedbackData(
                id=item.id,
                process_id=item.process_id,
                actor_user_id=item.actor_user_id,
                confirmed=None if item.confirmed is None else bool(item.confirmed),
                rating=item.rating,
                comment=item.comment,
                unresolved_flag=bool(item.unresolved_flag),
                created_at=item.created_at,
            )
            for item in feedbacks
        ],
        logs=[
            WorkOrderLogData(
                id=item.id,
                actor_user_id=item.actor_user_id,
                actor_type=item.actor_type,
                from_status=item.from_status,
                to_status=item.to_status,
                process_reason=item.process_reason,
                event_code=item.event_code,
                reason=(item.payload_json or {}).get("reason"),
                created_at=item.created_at,
            )
            for item in logs
        ],
    )
    detail.display = work_order_display_data(detail, display_context)
    return success_envelope(request, detail.model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/append")
def append_work_order(
    work_order_id: int,
    body: AppendWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.append_content(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        text=body.text,
        file_ids=body.file_ids,
        taken_phase=body.taken_phase,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/cancel")
def cancel_work_order(
    work_order_id: int,
    body: CancelWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.cancel(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        reason=body.reason,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/confirm")
def confirm_work_order(
    work_order_id: int,
    body: ConfirmWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.confirm(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/reopen")
def reopen_work_order(
    work_order_id: int,
    body: ReopenWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.reopen(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        reason=body.reason,
        version=body.version,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))


@router.post("/work-orders/{work_order_id}/rate")
def rate_work_order(
    work_order_id: int,
    body: RateWorkOrderRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkOrderService, Depends(get_work_order_service)],
) -> dict[str, object]:
    record = service.rate(
        actor=actor,
        work_order_id=work_order_id,
        request_id=request_id_for(request),
        rating=body.rating,
        comment=body.comment,
    )
    return success_envelope(request, work_order_data(record).model_dump(mode="json"))
