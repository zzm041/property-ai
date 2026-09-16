"""Property-scoped resident bind-request review routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, ResidentErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.resident import (
    BindRequestListData,
    RejectResidentBindRequest,
    ResidentBindRequestData,
)
from app.service.resident import ResidentBindingService

router = APIRouter(prefix="/admin", tags=["resident-binding-admin"])


def get_resident_binding_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> ResidentBindingService:
    return ResidentBindingService(session)


def _request_data(record) -> ResidentBindRequestData:
    return ResidentBindRequestData(
        id=record.id,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        house_id=record.house_id,
        relation_type=record.relation_type,
        status=record.status,
        reviewer_id=record.reviewer_id,
        review_reason=record.review_reason,
        requested_at=record.requested_at,
        reviewed_at=record.reviewed_at,
    )


def _reject_header_mismatch(
    *,
    property_id: int,
    x_property_id: str | None,
) -> None:
    if x_property_id is None or x_property_id == "":
        return
    try:
        declared = int(x_property_id)
    except ValueError as exc:
        raise AppError(
            code=ResidentErrorCode.INVALID_INPUT,
            message="X-Property-Id is invalid",
            status_code=422,
        ) from exc
    if declared != property_id:
        raise AppError(
            code=ResidentErrorCode.INVALID_INPUT,
            message="X-Property-Id does not match the target property",
            status_code=422,
        )


@router.get("/resident-bind-requests")
def list_pending_resident_bind_requests(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int, Query(gt=0)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    x_property_id: Annotated[str | None, Header(alias="X-Property-Id")] = None,
) -> dict[str, object]:
    _reject_header_mismatch(
        property_id=property_id,
        x_property_id=x_property_id,
    )
    records = service.list_pending_bind_requests(
        actor=actor,
        tenant_id=tenant_id,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
    )
    items = [_request_data(record) for record in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        BindRequestListData(items=items, next_cursor=next_cursor).model_dump(
            mode="json"
        ),
    )


@router.post("/resident-bind-requests/{request_id}/approve")
def approve_resident_bind_request(
    request_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
) -> dict[str, object]:
    record, _relation = service.approve_bind_request(
        actor=actor,
        request_id=request_id,
        http_request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _request_data(record).model_dump(mode="json"),
    )


@router.post("/resident-bind-requests/{request_id}/reject")
def reject_resident_bind_request(
    request_id: int,
    body: RejectResidentBindRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
) -> dict[str, object]:
    record = service.reject_bind_request(
        actor=actor,
        request_id=request_id,
        reason=body.reason,
        http_request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _request_data(record).model_dump(mode="json"),
    )
