"""Development-only authentication routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.auth import get_auth_service
from app.core.config import Settings, get_settings
from app.schema.auth import AccessTokenData, DevelopmentTokenRequest
from app.schema.common import success_envelope
from app.service.auth import AuthService

router = APIRouter(prefix="/auth/dev", tags=["development-auth"])


@router.post("/token")
def issue_development_token(
    body: DevelopmentTokenRequest,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    token = service.issue_development_token(body.user_id, settings)
    data = AccessTokenData(
        access_token=token.access_token,
        token_type="bearer",
        expires_in=token.expires_in,
    )
    return success_envelope(request, data.model_dump(mode="json"))
