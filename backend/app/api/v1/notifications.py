"""In-app notification list and mark-read."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import success_envelope
from app.schema.knowledge import NotificationData, NotificationListData
from app.service.notification import NotificationService

router = APIRouter(tags=["notifications"])


def get_notification_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> NotificationService:
    return NotificationService(session)


@router.get("/notifications")
def list_notifications(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items, unread, next_cursor = service.list_mine(
        actor=actor,
        after_id=after_id,
        limit=limit,
    )
    data = NotificationListData(
        items=[
            NotificationData(
                id=item.id,
                tenant_id=item.tenant_id,
                property_id=item.property_id,
                title=item.title,
                body=item.body,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                read_at=item.read_at,
                created_at=item.created_at,
            )
            for item in items
        ],
        unread_count=unread,
        next_cursor=next_cursor,
    )
    return success_envelope(request, data.model_dump(mode="json"))


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> dict[str, object]:
    record = service.mark_read(actor=actor, notification_id=notification_id)
    data = NotificationData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        title=record.title,
        body=record.body,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        read_at=record.read_at,
        created_at=record.created_at,
    )
    return success_envelope(request, data.model_dump(mode="json"))
