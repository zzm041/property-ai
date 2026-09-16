"""WeChat Mini Program login."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.schema.common import request_id_for, success_envelope
from app.schema.knowledge import AccessTokenWithMethodData, WeChatLoginRequest
from app.service.wechat_auth import WeChatAuthService

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
    )
    return success_envelope(request, data.model_dump(mode="json"))
