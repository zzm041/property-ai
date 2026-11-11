"""WeChat Mini Program login."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies.auth import get_current_principal
from app.core.errors import AppError, IamErrorCode
from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.domain.identity import Principal
from app.schema.auth import WeChatBindRequest, WeChatRebindRequest
from app.schema.common import request_id_for, success_envelope
from app.schema.knowledge import AccessTokenWithMethodData, WeChatLoginRequest
from app.service.identity.wechat_auth import WeChatAuthService

router = APIRouter(prefix="/auth/wechat", tags=["wechat-auth"])


@router.post("/login")
def wechat_login(
    body: WeChatLoginRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    session = get_session_factory(settings.active_database_url)()
    try:
        token = WeChatAuthService(session).login(
            code=body.code,
            settings=settings,
            request_id=request_id_for(request),
        )
    finally:
        session.close()
    data = AccessTokenWithMethodData(
        access_token=token.access_token,
        token_type="bearer",
        expires_in=token.expires_in,
        auth_method="wechat",
        refresh_token=getattr(token, "refresh_token", None),
    )
    return success_envelope(request, data.model_dump(mode="json"))


@router.post("/bind")
def wechat_bind(
    body: WeChatBindRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    if actor.auth_method not in {"sms", "password"}:
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="WeChat bind requires a verified phone session",
            status_code=403,
        )
    session = get_session_factory(settings.active_database_url)()
    try:
        record = WeChatAuthService(session).bind(
            user_id=actor.user_id,
            code=body.code,
            settings=settings,
            request_id=request_id_for(request),
        )
    finally:
        session.close()
    return success_envelope(
        request,
        {"user_id": record.user_id, "appid": record.appid},
    )


@router.post("/rebind")
def wechat_rebind(
    body: WeChatRebindRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    if actor.auth_method not in {"sms", "password", "wechat"}:
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="WeChat rebind requires an official authenticated session",
            status_code=403,
        )
    session = get_session_factory(settings.active_database_url)()
    try:
        issued = WeChatAuthService(session).rebind(
            user_id=actor.user_id,
            code=body.code,
            phone=body.phone,
            sms_code=body.sms_code,
            settings=settings,
            request_id=request_id_for(request),
        )
    finally:
        session.close()
    return success_envelope(request, issued.as_schema().model_dump(mode="json"))
