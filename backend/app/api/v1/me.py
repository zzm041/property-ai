"""Current authenticated Principal endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.auth import get_current_principal
from app.domain.iam import Principal
from app.schema.common import success_envelope
from app.schema.identity import GrantedScopeData, MeData

router = APIRouter(tags=["identity"])


@router.get("/me")
def get_me(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    ordered_grants = sorted(
        principal.grants,
        key=lambda grant: (
            grant.role_code,
            grant.scope.scope_type.value,
            grant.scope.tenant_id or 0,
            grant.scope.property_id or 0,
        ),
    )
    data = MeData(
        user_id=principal.user_id,
        status=principal.status,
        display_name=principal.display_name,
        roles=sorted(principal.roles),
        permissions=sorted(principal.permissions),
        scopes=[
            GrantedScopeData(
                role_code=grant.role_code,
                scope_type=grant.scope.scope_type,
                tenant_id=grant.scope.tenant_id,
                property_id=grant.scope.property_id,
                permissions=sorted(grant.permissions),
            )
            for grant in ordered_grants
        ],
    )
    return success_envelope(request, data.model_dump(mode="json"))
