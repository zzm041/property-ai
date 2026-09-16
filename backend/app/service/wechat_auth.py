"""WeChat Mini Program login. Never stores session_key or returns it."""

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode, ErrorCode, WeChatAuthErrorCode
from app.core.security import issue_access_token
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.model.identity import User, UserWechat
from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError
from app.provider.factory import build_wechat_auth_provider
from app.repository.identity import IdentityRepository
from app.repository.knowledge import KnowledgeRepository
from app.service.audit import AuditService
from app.service.auth import IssuedAccessToken


class WeChatAuthService:
    def __init__(
        self,
        session: Session,
        repository: KnowledgeRepository | None = None,
        identity_repository: IdentityRepository | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or KnowledgeRepository(session)
        self._identity = identity_repository or IdentityRepository(session)
        self._audit = audit_service or AuditService(session)

    def login(
        self,
        *,
        code: str,
        settings: Settings,
        request_id: str,
    ) -> IssuedAccessToken:
        if not settings.wechat_login_enabled:
            raise AppError(
                code=WeChatAuthErrorCode.UNAVAILABLE,
                message="WeChat login is unavailable",
                status_code=503,
            )
        provider = build_wechat_auth_provider(settings)
        try:
            session = provider.exchange_code(code.strip())
        except ProviderInvalidOutputError as exc:
            raise AppError(
                code=WeChatAuthErrorCode.INVALID_CODE,
                message="WeChat code is invalid",
                status_code=401,
            ) from exc
        except ProviderUnavailableError as exc:
            raise AppError(
                code=WeChatAuthErrorCode.UNAVAILABLE,
                message="WeChat auth is unavailable",
                status_code=503,
            ) from exc
        if self._session.in_transaction():
            self._session.rollback()
        try:
            with self._session.begin():
                binding = self._repository.get_user_wechat(
                    appid=session.appid,
                    openid=session.openid,
                )
                if binding is None and session.unionid:
                    binding = self._repository.get_user_wechat_by_unionid(
                        session.unionid
                    )
                if binding is None:
                    user = self._repository.add_user(
                        User(status="ACTIVE", display_name="微信用户")
                    )
                    binding = self._repository.add_user_wechat(
                        UserWechat(
                            user_id=user.id,
                            appid=session.appid,
                            openid=session.openid,
                            unionid=session.unionid,
                        )
                    )
                user = self._identity.get_user(binding.user_id)
                if user is None or user.status != "ACTIVE":
                    raise AppError(
                        code=AuthErrorCode.USER_DISABLED
                        if user is not None
                        else AuthErrorCode.INVALID_USER,
                        message="User is not active",
                        status_code=401,
                    )
                token = issue_access_token(
                    user.id,
                    settings,
                    auth_method="wechat",
                )
                self._audit.append(
                    action=AuditAction.WECHAT_AUTH_TOKEN_ISSUED,
                    resource_type=AuditResourceType.USER,
                    actor_user_id=user.id,
                    resource_id=user.id,
                    request_id=request_id,
                    scope_type=ScopeType.SYSTEM,
                    tenant_id=None,
                    property_id=None,
                    details={
                        "auth_method": "wechat",
                        "token_type": "access",
                    },
                )
                return IssuedAccessToken(
                    access_token=token,
                    token_type="bearer",
                    expires_in=settings.jwt_access_token_ttl_seconds,
                )
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=WeChatAuthErrorCode.INVALID_CODE,
                message="WeChat identity conflicted",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="WeChat login could not be completed",
                status_code=500,
            ) from exc
