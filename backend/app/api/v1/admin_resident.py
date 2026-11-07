"""Property-scoped resident bind-request review routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, ResidentErrorCode
from app.domain.iam import Principal
from app.model.resident import ResidentBindRequest
from app.repository.resident import ResidentBindReviewDetails
from app.schema.common import request_id_for, success_envelope
from app.schema.resident import (
    BindReviewListData,
    RejectResidentBindRequest,
    ResidentBindReviewData,
    ResidentBindRequestData,
)
from app.schema.property import (
    EndResidentRelationRequest,
    ResidentRelationAdminData,
    ResidentRelationHistoryData,
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


def _review_request_data(
    record: ResidentBindRequest,
    details: ResidentBindReviewDetails,
) -> ResidentBindReviewData:
    return ResidentBindReviewData(
        **_request_data(record).model_dump(),
        applicant_display_name=details.applicant_display_name,
        property_name=details.property_name,
        building_name=details.building_name,
        unit_name=details.unit_name,
        room_no=details.room_no,
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
    records = service.list_pending_bind_review_items(
        actor=actor,
        tenant_id=tenant_id,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
    )
    items = [_review_request_data(record, details) for record, details in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        BindReviewListData(items=items, next_cursor=next_cursor).model_dump(
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


@router.get("/resident-house-relations")
def list_resident_house_relations(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int, Query(gt=0)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    house_id: Annotated[int | None, Query(gt=0)] = None,
    user_id: Annotated[int | None, Query(gt=0)] = None,
) -> dict[str, object]:
    pairs = service.list_property_relation_history(
        actor=actor,
        tenant_id=tenant_id,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
        house_id=house_id,
        user_id=user_id,
    )
    items = [
        ResidentRelationAdminData(
            id=relation.id,
            user_id=relation.user_id,
            user_display_name=user.display_name,
            house_id=house.id,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            unit_id=house.unit_id,
            room_no=house.room_no,
            relation_type=relation.relation_type,
            status=relation.status,
            valid_from=relation.valid_from,
            valid_to=relation.valid_to,
            bind_request_id=relation.bind_request_id,
        )
        for relation, house, user in pairs
    ]
    return success_envelope(
        request,
        ResidentRelationHistoryData(
            items=items,
            next_cursor=items[-1].id if len(items) == limit else None,
        ).model_dump(mode="json"),
    )


@router.post("/resident-house-relations/{relation_id}/end")
def end_resident_house_relation(
    relation_id: int,
    body: EndResidentRelationRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
) -> dict[str, object]:
    relation = service.end_relation_by_id(
        actor=actor,
        relation_id=relation_id,
        status=body.status.value,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        {
            "id": relation.id,
            "status": relation.status,
            "valid_to": relation.valid_to,
        },
    )
