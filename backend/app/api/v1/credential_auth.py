"""Official SMS/password session routes. Not Development Auth."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.core.rate_limit import MemoryRateLimiter
from app.domain.iam import Principal
from app.schema.auth import (
    LogoutRequest,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordResetRequest,
    ProfileUpdateRequest,
    SmsLoginRequest,
    SmsSendRequest,
    TokenRefreshRequest,
)
from app.schema.common import request_id_for, success_envelope
from app.schema.identity import MeData
from app.service.credential_auth import CredentialAuthService

router = APIRouter(prefix="/auth", tags=["credential-auth"])


def get_credential_auth_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CredentialAuthService:
    limiter = MemoryRateLimiter() if settings.app_env == "test" else None
    return CredentialAuthService(session, settings=settings, rate_limiter=limiter)


@router.post("/sms/send")
def send_sms(
    body: SmsSendRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    data = service.send_sms(
        phone=body.phone,
        purpose=body.purpose,
        request_id=request_id_for(request),
    )
    return success_envelope(request, data.model_dump(mode="json"))


@router.post("/sms/login")
def sms_login(
    body: SmsLoginRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.login_sms(
        phone=body.phone,
        code=body.code,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/password/login")
def password_login(
    body: PasswordLoginRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.login_password(
        phone=body.phone,
        password=body.password,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/password/change")
def password_change(
    body: PasswordChangeRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.change_password(
        user_id=actor.user_id,
        old_password=body.old_password,
        new_password=body.new_password,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/password/reset")
def password_reset(
    body: PasswordResetRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.reset_password(
        phone=body.phone,
        code=body.code,
        new_password=body.new_password,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/token/refresh")
def refresh_token(
    body: TokenRefreshRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.refresh(
        refresh_token=body.refresh_token,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/logout")
def logout(
    body: LogoutRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    service.logout(
        session_id=None,
        refresh_token=body.refresh_token,
        request_id=request_id_for(request),
    )
    return success_envelope(request, {"ok": True})
