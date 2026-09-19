"""Official SMS/password session routes. Not Development Auth."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.config import Settings, get_settings
from app.core.errors import AppError, AuthErrorCode
from app.core.rate_limit import MemoryRateLimiter
from app.domain.iam import Principal
from app.domain.phone import normalize_phone
from app.provider.sms import development_sms_inbox
from app.schema.auth import (
    LogoutRequest,
    PasswordChangeRequest,
    PhoneChangeRequest,
    PasswordLoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetVerifyRequest,
    SmsLoginRequest,
    SmsSendRequest,
    TokenRefreshRequest,
    VisualChallengeRequest,
)
from app.schema.common import request_id_for, success_envelope
from app.service.credential_auth import CredentialAuthService

router = APIRouter(prefix="/auth", tags=["credential-auth"])


def get_credential_auth_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CredentialAuthService:
    limiter = MemoryRateLimiter() if settings.app_env == "test" else None
    return CredentialAuthService(session, settings=settings, rate_limiter=limiter)


@router.post("/challenge")
def issue_visual_challenge(
    body: VisualChallengeRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    data = service.issue_visual_challenge(
        phone=body.phone,
        purpose=body.purpose,
        request_id=request_id_for(request),
    )
    return success_envelope(request, data.model_dump(mode="json"))


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
        challenge_id=body.challenge_id,
        challenge_answer=body.challenge_answer,
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


@router.post("/password/reset/verify")
def verify_password_reset(
    body: PasswordResetVerifyRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    token = service.verify_password_reset(
        phone=body.phone,
        code=body.code,
        request_id=request_id_for(request),
    )
    return success_envelope(
        request, {"reset_token": token, "expires_in": 600}
    )


@router.post("/phone/change")
def phone_change(
    body: PhoneChangeRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    issued = service.change_phone(
        user_id=actor.user_id,
        old_phone=body.old_phone,
        old_code=body.old_code,
        new_phone=body.new_phone,
        new_code=body.new_code,
        request_id=request_id_for(request),
    )
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))


@router.post("/password/reset/confirm")
def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    request: Request,
    service: Annotated[CredentialAuthService, Depends(get_credential_auth_service)],
) -> dict[str, object]:
    service.confirm_password_reset(
        reset_token=body.reset_token,
        new_password=body.new_password,
        confirm_password=body.confirm_password,
        request_id=request_id_for(request),
    )
    return success_envelope(request, {"ok": True})


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


@router.post("/sms/test-inbox")
def sms_test_inbox(
    body: dict[str, str],
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    if settings.app_env != "test" or settings.sms_provider != "development":
        raise AppError(
            code=AuthErrorCode.DEV_AUTH_DISABLED,
            message="Test inbox is unavailable",
            status_code=404,
        )
    phone = normalize_phone(body.get("phone") or "")
    purpose = body.get("purpose") or "login"
    if phone is None:
        raise AppError(
            code=AuthErrorCode.INVALID_PHONE,
            message="Phone number is invalid",
            status_code=422,
        )
    code = development_sms_inbox().last_code(phone, purpose)
    return success_envelope(request, {"code": code})
