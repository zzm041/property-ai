"""Scoped operations statistics route for property and tenant administrators."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import success_envelope
from app.service.operations import OperationsService

router = APIRouter(prefix="/admin", tags=["admin-operations"])


def get_operations_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> OperationsService:
    return OperationsService(session)


@router.get("/operations-overview")
def get_operations_overview(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[OperationsService, Depends(get_operations_service)],
    scope_type: Annotated[Literal["TENANT", "PROPERTY"], Query()],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int | None, Query(gt=0)] = None,
) -> dict[str, object]:
    result = service.get_overview(
        actor=actor,
        scope_type=scope_type,
        tenant_id=tenant_id,
        property_id=property_id,
    )
    return success_envelope(request, result.model_dump(mode="json"))
