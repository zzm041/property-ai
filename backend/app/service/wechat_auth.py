"""WeChat Mini Program login. Never stores session_key or returns it."""

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import run_owned_transaction
from app.core.errors import AppError, AuthErrorCode, ErrorCode, WeChatAuthErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.model.identity import UserWechat
from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError
from app.provider.factory import build_wechat_auth_provider
from app.repository.identity import IdentityRepository
from app.repository.knowledge import KnowledgeRepository
from app.service.audit import AuditService
from app.service.credential_auth import CredentialAuthService, IssuedSession


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
    ) -> IssuedSession:
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
        issued: IssuedSession | None = None
        try:

            def _persist() -> None:
                nonlocal issued
                binding = self._repository.get_user_wechat(
                    appid=session.appid,
                    openid=session.openid,
                )
                if binding is None and session.unionid:
                    binding = self._repository.get_user_wechat_by_unionid(
                        session.unionid
                    )
                if binding is None:
                    raise AppError(
                        code=AuthErrorCode.WECHAT_BIND_REQUIRED,
                        message="WeChat identity must be bound to a verified account",
                        status_code=401,
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
                issued = CredentialAuthService(
                    self._session, settings=settings
                )._issue_session(user_id=user.id, auth_method="wechat")
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

            run_owned_transaction(self._session, _persist)
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
        if issued is None:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="WeChat login could not be completed",
                status_code=500,
            )
        return issued

    def bind(
        self,
        *,
        user_id: int,
        code: str,
        settings: Settings,
        request_id: str,
    ) -> UserWechat:
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
        bound: UserWechat | None = None

        def _persist() -> None:
            nonlocal bound
            user = self._identity.get_user(user_id)
            if user is None or user.status != "ACTIVE":
                raise AppError(
                    code=AuthErrorCode.USER_DISABLED,
                    message="User is not active",
                    status_code=401,
                )
            existing = self._repository.get_user_wechat(
                appid=session.appid,
                openid=session.openid,
            )
            if existing is not None and existing.user_id != user.id:
                raise AppError(
                    code=AuthErrorCode.WECHAT_ALREADY_BOUND,
                    message="WeChat identity is already bound",
                    status_code=409,
                )
            if existing is not None:
                bound = existing
                return
            bound = self._repository.add_user_wechat(
                UserWechat(
                    user_id=user.id,
                    appid=session.appid,
                    openid=session.openid,
                    unionid=session.unionid,
                )
            )
            self._audit.append(
                action=AuditAction.AUTH_WECHAT_BOUND,
                resource_type=AuditResourceType.USER_WECHAT,
                actor_user_id=user.id,
                resource_id=bound.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={"auth_method": "wechat"},
            )

        try:
            run_owned_transaction(self._session, _persist)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=AuthErrorCode.WECHAT_ALREADY_BOUND,
                message="WeChat identity is already bound",
                status_code=409,
            ) from exc
        if bound is None:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="WeChat bind could not be completed",
                status_code=500,
            )
        return bound
