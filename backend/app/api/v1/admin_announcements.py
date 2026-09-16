"""Admin announcement draft, review, and publish."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.ai import (
    AnnouncementData,
    CreateAnnouncementRequest,
    PublishAnnouncementRequest,
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
        audience=body.audience,
    )
    return success_envelope(request, announcement_data(record).model_dump(mode="json"))


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
