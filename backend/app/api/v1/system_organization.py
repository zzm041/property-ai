"""System administrator organization onboarding route."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.organization import (
    CreateOrganizationRequest,
    OrganizationOnboardingData,
)
from app.service.organization import OrganizationOnboardingService

router = APIRouter(prefix="/system", tags=["system-organization"])


def get_organization_onboarding_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> OrganizationOnboardingService:
    return OrganizationOnboardingService(session)


@router.post("/organizations")
def create_organization(
    body: CreateOrganizationRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[
        OrganizationOnboardingService,
        Depends(get_organization_onboarding_service),
    ],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    result = service.create_organization(
        actor=actor,
        tenant_name=body.tenant_name,
        tenant_contact=body.tenant_contact,
        property_name=body.property_name,
        property_address=body.property_address,
        timezone=body.timezone,
        initial_admin_phone=body.initial_admin_phone,
        idempotency_key=idempotency_key,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        OrganizationOnboardingData(
            tenant_id=result.tenant.id,
            tenant_name=result.tenant.name,
            property_id=result.property_record.id,
            property_name=result.property_record.name,
            initial_admin_user_id=result.initial_admin_user_id,
            initial_admin_role="TENANT_ADMIN",
        ).model_dump(mode="json"),
    )
