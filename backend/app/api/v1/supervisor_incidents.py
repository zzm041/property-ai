"""Supervisor incident list, detail, and AI summary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.domain.incident import IncidentStatus
from app.schema.ai import IncidentData, IncidentListData, UpdateIncidentStatusRequest
from app.schema.common import request_id_for, success_envelope
from app.service.incident import IncidentService

router = APIRouter(prefix="/supervisor", tags=["supervisor-incidents"])


def get_incident_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> IncidentService:
    return IncidentService(session)


def incident_data(record) -> IncidentData:
    return IncidentData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        building_id=record.building_id,
        status=record.status,
        category=record.category,
        subcategory=getattr(record, "subcategory", None),
        summary=record.summary,
        ai_summary=record.ai_summary,
        affected_scope=record.affected_scope,
        primary_work_order_id=record.primary_work_order_id,
        primary_employee_id=getattr(record, "primary_employee_id", None),
        work_order_count=record.work_order_count,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
        updated_at=getattr(record, "updated_at", record.last_seen_at),
    )


@router.get("/incidents")
def list_incidents(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IncidentService, Depends(get_incident_service)],
    property_id: Annotated[int, Query(gt=0)],
    tenant_id: Annotated[int | None, Query(gt=0)] = None,
    status: IncidentStatus | None = None,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    records = service.list_incidents(
        actor=actor,
        property_id=property_id,
        tenant_id=tenant_id,
        status=None if status is None else status.value,
        after_id=after_id,
        limit=limit,
    )
    items = [incident_data(item) for item in records]
    next_cursor = items[-1].id if len(items) == limit else None
    return success_envelope(
        request,
        IncidentListData(items=items, next_cursor=next_cursor).model_dump(mode="json"),
    )


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> dict[str, object]:
    record = service.get_incident(actor=actor, incident_id=incident_id)
    return success_envelope(request, incident_data(record).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/summary")
def summarize_incident(
    incident_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> dict[str, object]:
    record = service.summarize(
        actor=actor,
        incident_id=incident_id,
        request_id=request_id_for(request),
    )
    return success_envelope(request, incident_data(record).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/status")
def update_incident_status(
    incident_id: int,
    body: UpdateIncidentStatusRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> dict[str, object]:
    record = service.update_status(
        actor=actor,
        incident_id=incident_id,
        target_status=body.status,
        reason=body.reason,
        expected_updated_at=body.expected_updated_at,
        request_id=request_id_for(request),
    )
    return success_envelope(request, incident_data(record).model_dump(mode="json"))
