"""Admin announcement draft, review, and publish."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.ai import (
    AdminAnnouncementData,
    AdminAnnouncementListData,
    AnnouncementData,
    AnnouncementHistoryData,
    AnnouncementReviewData,
    AnnouncementScopeData,
    CreateAnnouncementRequest,
    PublishAnnouncementRequest,
    UpdateAnnouncementRequest,
)
from app.schema.common import request_id_for, success_envelope
from app.service.announcement import AnnouncementService

router = APIRouter(prefix="/admin", tags=["admin-announcements"])


def get_announcement_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> AnnouncementService:
    return AnnouncementService(session)


def announcement_data(record) -> AnnouncementData:
    return AnnouncementData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        incident_id=record.incident_id,
        title=record.title,
        content=record.content,
        status=record.status,
        ai_draft=bool(record.ai_draft),
        published_at=record.published_at,
        created_at=record.created_at,
    )


def admin_announcement_data(detail) -> AdminAnnouncementData:
    record, scopes, reviews, history = detail
    public = announcement_data(record)
    return AdminAnnouncementData(
        **public.model_dump(),
        created_by_user_id=record.created_by_user_id,
        reviewed_by_user_id=record.reviewed_by_user_id,
        published_by_user_id=record.published_by_user_id,
        updated_at=record.updated_at,
        scopes=[
            AnnouncementScopeData(
                id=item.id,
                scope_type=item.scope_type,
                building_id=item.building_id,
                unit_id=item.unit_id,
                audience=item.audience,
            )
            for item in scopes
        ],
        reviews=[
            AnnouncementReviewData(
                id=item.id,
                reviewer_user_id=item.reviewer_user_id,
                decision=item.decision,
                reason=item.reason,
                created_at=item.created_at,
            )
            for item in reviews
        ],
        history=[
            AnnouncementHistoryData(
                id=item.id,
                action=item.action,
                actor_user_id=item.actor_user_id,
                details=item.details_json or {},
                created_at=item.created_at,
            )
            for item in history
        ],
    )


@router.get("/announcements")
def list_admin_announcements(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
    property_id: Annotated[int, Query(gt=0)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    details = service.list_managed(
        actor=actor,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
    )
    items = [admin_announcement_data(item) for item in details]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        AdminAnnouncementListData(
            items=items,
            next_cursor=next_cursor,
        ).model_dump(mode="json"),
    )


@router.get("/announcements/{announcement_id}")
def get_admin_announcement(
    announcement_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
) -> dict[str, object]:
    detail = service.get_managed(actor=actor, announcement_id=announcement_id)
    return success_envelope(
        request,
        admin_announcement_data(detail).model_dump(mode="json"),
    )


@router.post("/announcements")
def create_announcement(
    body: CreateAnnouncementRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
) -> dict[str, object]:
    record = service.create_draft(
        actor=actor,
        property_id=body.property_id,
        request_id=request_id_for(request),
        title=body.title,
        content=body.content,
        incident_id=body.incident_id,
        ai_draft=body.ai_draft,
        claimed_tenant_id=body.tenant_id,
        scope_type=body.scope_type,
        building_id=body.building_id,
        unit_id=body.unit_id,
        audience=body.audience,
    )
    return success_envelope(request, announcement_data(record).model_dump(mode="json"))


@router.patch("/announcements/{announcement_id}")
def update_announcement(
    announcement_id: int,
    body: UpdateAnnouncementRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
) -> dict[str, object]:
    record = service.update_draft(
        actor=actor,
        announcement_id=announcement_id,
        title=body.title,
        content=body.content,
        scope_type=body.scope_type,
        building_id=body.building_id,
        unit_id=body.unit_id,
        audience=body.audience,
        request_id=request_id_for(request),
    )
    detail = service.get_managed(actor=actor, announcement_id=record.id)
    return success_envelope(
        request,
        admin_announcement_data(detail).model_dump(mode="json"),
    )


@router.post("/announcements/{announcement_id}/submit-review")
def submit_announcement_review(
    announcement_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
) -> dict[str, object]:
    record = service.submit_review(
        actor=actor,
        announcement_id=announcement_id,
        request_id=request_id_for(request),
    )
    return success_envelope(request, announcement_data(record).model_dump(mode="json"))


@router.post("/announcements/{announcement_id}/publish")
def publish_announcement(
    announcement_id: int,
    body: PublishAnnouncementRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
) -> dict[str, object]:
    record = service.publish(
        actor=actor,
        announcement_id=announcement_id,
        request_id=request_id_for(request),
        decision=body.decision,
        reason=body.reason,
    )
    return success_envelope(request, announcement_data(record).model_dump(mode="json"))
