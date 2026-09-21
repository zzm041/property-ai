"""Admin SLA policy management routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.sla import (
    EffectiveSlaPolicyData,
    PutSlaPolicyRequest,
    SlaPolicyData,
    SlaPolicyListData,
)
from app.service.sla_policy import SlaPolicyListing, SlaPolicyService

router = APIRouter(prefix="/admin", tags=["admin-sla"])


def get_sla_policy_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> SlaPolicyService:
    return SlaPolicyService(session)


def _policy_data(record) -> SlaPolicyData:
    return SlaPolicyData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        scope_type="TENANT" if record.property_id is None else "PROPERTY",
        category=record.category,
        priority=record.priority,
        respond_minutes=record.respond_minutes,
        complete_minutes=record.complete_minutes,
        status=record.status,
        updated_at=record.updated_at,
    )


def _listing_data(listing: SlaPolicyListing) -> SlaPolicyListData:
    return SlaPolicyListData(
        property_id=listing.property_id,
        items=[_policy_data(record) for record in listing.items],
        effective=[
            EffectiveSlaPolicyData(
                category=item.category,
                priority=item.priority,
                source=item.source,
                policy_id=item.policy_id,
                respond_minutes=item.respond_minutes,
                complete_minutes=item.complete_minutes,
            )
            for item in listing.effective
        ],
    )


@router.get("/sla-policies")
def list_sla_policies(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SlaPolicyService, Depends(get_sla_policy_service)],
    property_id: Annotated[int, Query(gt=0)],
    tenant_id: Annotated[int | None, Query(gt=0)] = None,
) -> dict[str, object]:
    listing = service.list_for_property(
        actor=actor,
        property_id=property_id,
        claimed_tenant_id=tenant_id,
    )
    return success_envelope(
        request,
        _listing_data(listing).model_dump(mode="json"),
    )


@router.put("/sla-policies")
def put_sla_policy(
    body: PutSlaPolicyRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SlaPolicyService, Depends(get_sla_policy_service)],
) -> dict[str, object]:
    record = service.put_policy(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        category=body.category,
        priority=body.priority,
        respond_minutes=body.respond_minutes,
        complete_minutes=body.complete_minutes,
        status=body.status,
        expected_updated_at=body.expected_updated_at,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        _policy_data(record).model_dump(mode="json"),
    )
