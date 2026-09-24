"""Current authenticated Principal endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.domain.iam import Principal
from app.repository.identity import IdentityRepository
from app.schema.auth import ProfileUpdateRequest
from app.schema.common import request_id_for, success_envelope
from app.schema.identity import GrantedScopeData, MeData
from app.service.credential_auth import CredentialAuthService

router = APIRouter(tags=["identity"])


@router.get("/me/contact-phone")
def get_contact_default(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[Session, Depends(get_db_session)],
) -> dict[str, object]:
    from app.repository.credential import CredentialRepository

    response.headers["Cache-Control"] = "no-store"
    phone = CredentialRepository(session).get_active_phone_for_user(principal.user_id)
    return success_envelope(request, {"phone": None if phone is None else phone.phone_e164})


def _me_data(principal: Principal, *, profile_completed: bool) -> MeData:
    ordered_grants = sorted(
        principal.grants,
        key=lambda grant: (
            grant.role_code,
            grant.scope.scope_type.value,
            grant.scope.tenant_id or 0,
            grant.scope.property_id or 0,
        ),
    )
    return MeData(
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
        profile_completed=profile_completed,
    )


@router.get("/me")
def get_me(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[Session, Depends(get_db_session)],
) -> dict[str, object]:
    user = IdentityRepository(session).get_user(principal.user_id)
    completed = bool(
        user is not None
        and user.display_name
        and not user.display_name.startswith("微信")
        and "****" not in user.display_name
    )
    return success_envelope(
        request,
        _me_data(principal, profile_completed=completed).model_dump(mode="json"),
    )


@router.patch("/me/profile")
def update_profile(
    body: ProfileUpdateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    user = CredentialAuthService(session, settings=settings).update_profile(
        user_id=principal.user_id,
        display_name=body.display_name,
        request_id=request_id_for(request),
    )
    refreshed = Principal(
        user_id=principal.user_id,
        status=principal.status,
        display_name=user.display_name,
        grants=principal.grants,
    )
    return success_envelope(
        request,
        _me_data(refreshed, profile_completed=True).model_dump(mode="json"),
    )
