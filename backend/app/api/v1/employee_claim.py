"""Authenticated employee invite claim."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.identity import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.employee import ClaimEmployeeInviteRequest, EmployeeData
from app.service.employee_identity import EmployeeIdentityService

router = APIRouter(prefix="/staff", tags=["employee-claim"])


@router.post("/employee-claim")
def claim_employee(
    body: ClaimEmployeeInviteRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    record = EmployeeIdentityService(
        session, secret=settings.app_secret_key.get_secret_value()
    ).claim(actor=actor, token=body.token, request_id=request_id_for(request))
    data = EmployeeData(
        id=record.id,
        user_id=record.user_id,
        tenant_id=record.tenant_id,
        employee_no=record.employee_no,
        department_id=record.department_id,
        status=record.status,
        max_capacity=record.max_capacity,
    )
    return success_envelope(request, data.model_dump(mode="json"))
