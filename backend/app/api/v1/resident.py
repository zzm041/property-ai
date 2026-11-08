"""Resident bind-request and current-house routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.resident import (
    BindRequestListData,
    CreateResidentBindRequest,
    HouseRelationListData,
    ResidentBindRequestData,
    ResidentHouseData,
)
from app.service.resident import ResidentBindingService

router = APIRouter(tags=["resident-binding"])


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


@router.post("/resident-bind-requests")
def create_resident_bind_request(
    body: CreateResidentBindRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
) -> dict[str, object]:
    record = service.create_bind_request(
        actor=actor,
        house_id=body.house_id,
        relation_type=body.relation_type.value,
        request_id=request_id_for(request),
        claimed_tenant_id=body.tenant_id,
        claimed_property_id=body.property_id,
    )
    return success_envelope(
        request,
        _request_data(record).model_dump(mode="json"),
    )


@router.get("/resident-bind-requests/mine")
def list_my_resident_bind_requests(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_my_bind_requests(
        actor=actor,
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


@router.get("/me/houses")
def list_my_houses(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        ResidentBindingService, Depends(get_resident_binding_service)
    ],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    views = service.list_my_house_views(actor=actor, after_id=after_id, limit=limit)
    items = [
        ResidentHouseData(
            relation_id=relation.id,
            house_id=house.id,
            tenant_id=house.tenant_id,
            property_id=house.property_id,
            room_no=house.room_no,
            relation_type=relation.relation_type,
            status=relation.status,
            valid_from=relation.valid_from,
            valid_to=relation.valid_to,
            **address,
        )
        for relation, house, address in views
    ]
    next_cursor = items[-1].relation_id if len(items) == limit else None
    return success_envelope(
        request,
        HouseRelationListData(items=items, next_cursor=next_cursor).model_dump(
            mode="json"
        ),
    )
