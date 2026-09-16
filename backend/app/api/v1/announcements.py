"""Resident-facing published announcements."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.api.v1.admin_announcements import announcement_data, get_announcement_service
from app.domain.iam import Principal
from app.schema.ai import AnnouncementListData
from app.schema.common import success_envelope
from app.service.announcement import AnnouncementService

router = APIRouter(tags=["announcements"])


@router.get("/announcements")
def list_announcements(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AnnouncementService, Depends(get_announcement_service)],
    property_id: Annotated[int, Query(gt=0)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_published(
        actor=actor,
        property_id=property_id,
        after_id=after_id,
        limit=limit,
    )
    items = [announcement_data(item) for item in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        AnnouncementListData(items=items, next_cursor=next_cursor).model_dump(mode="json"),
    )
