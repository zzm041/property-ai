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
from app.repository.credential import CredentialRepository
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
        credential_service = CredentialAuthService(self._session, settings=settings)
        bound: UserWechat | None = None

        def _persist() -> None:
            nonlocal bound
            try:
                user = credential_service._lock_active_user(user_id)
            except AppError as exc:
                raise AppError(
                    code=AuthErrorCode.USER_DISABLED,
                    message="User is not active",
                    status_code=401,
                ) from exc
            candidates = self._repository.list_user_wechat_rebind_candidates_for_update(
                user_id=user.id,
                appid=session.appid,
                openid=session.openid,
                unionid=session.unionid,
            )
            existing = next(
                (
                    item
                    for item in candidates
                    if (
                        item.appid == session.appid
                        and item.openid == session.openid
                    )
                    or (session.unionid is not None and item.unionid == session.unionid)
                ),
                None,
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
            if any(
                item.user_id == user.id and item.appid == session.appid
                for item in candidates
            ):
                raise AppError(
                    code=AuthErrorCode.WECHAT_REBIND_REQUIRED,
                    message="Use verified WeChat rebind for an existing binding",
                    status_code=409,
                )
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

    def rebind(
        self,
        *,
        user_id: int,
        code: str,
        phone: str,
        sms_code: str,
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
            wechat_session = provider.exchange_code(code.strip())
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

        credential_repository = CredentialRepository(self._session)
        credential_service = CredentialAuthService(
            self._session,
            settings=settings,
            repository=credential_repository,
        )
        phone_e164 = credential_service._require_phone(phone)
        otp_status = {"value": "ok"}

        def _verify_phone() -> None:
            current_phone = (
                credential_repository.get_active_phone_for_user_for_update(user_id)
            )
            if current_phone is None or current_phone.phone_e164 != phone_e164:
                raise AppError(
                    code=AuthErrorCode.OLD_PHONE_MISMATCH,
                    message="Phone does not match the signed-in account",
                    status_code=409,
                )
            otp_status["value"] = credential_service._apply_otp(
                phone_e164,
                purpose="login",
                code=sms_code,
            )

        run_owned_transaction(self._session, _verify_phone)
        credential_service._raise_otp(otp_status["value"])

        issued_box: list[IssuedSession] = []

        def _persist() -> None:
            credential_service._lock_active_user(user_id)
            current_phone = (
                credential_repository.get_active_phone_for_user_for_update(user_id)
            )
            if current_phone is None or current_phone.phone_e164 != phone_e164:
                raise AppError(
                    code=AuthErrorCode.OLD_PHONE_MISMATCH,
                    message="Phone does not match the signed-in account",
                    status_code=409,
                )
            candidates = self._repository.list_user_wechat_rebind_candidates_for_update(
                user_id=user_id,
                appid=wechat_session.appid,
                openid=wechat_session.openid,
                unionid=wechat_session.unionid,
            )
            current_bindings = [
                item
                for item in candidates
                if item.user_id == user_id and item.appid == wechat_session.appid
            ]
            if not current_bindings:
                raise AppError(
                    code=AuthErrorCode.WECHAT_BIND_REQUIRED,
                    message="No existing WeChat binding is available to replace",
                    status_code=409,
                )
            target = next(
                (
                    item
                    for item in candidates
                    if (
                        item.appid == wechat_session.appid
                        and item.openid == wechat_session.openid
                    )
                    or (
                        wechat_session.unionid is not None
                        and item.unionid == wechat_session.unionid
                    )
                ),
                None,
            )
            if target is not None and target.user_id != user_id:
                raise AppError(
                    code=AuthErrorCode.WECHAT_ALREADY_BOUND,
                    message="WeChat identity is already bound; use account merge",
                    status_code=409,
                )
            if target is not None:
                raise AppError(
                    code=AuthErrorCode.WECHAT_SAME_BINDING,
                    message="New WeChat identity must differ from the current binding",
                    status_code=409,
                )

            keeper = current_bindings[0]
            for stale in current_bindings[1:]:
                self._session.delete(stale)
            keeper.openid = wechat_session.openid
            keeper.unionid = wechat_session.unionid
            self._session.flush()
            credential_service._revoke_sessions(
                user_id,
                reason="WECHAT_REBIND",
                request_id=request_id,
            )
            issued = credential_service._issue_session(
                user_id=user_id,
                auth_method="sms",
            )
            self._audit.append(
                action=AuditAction.AUTH_WECHAT_REBOUND,
                resource_type=AuditResourceType.USER_WECHAT,
                actor_user_id=user_id,
                resource_id=keeper.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                details={
                    "auth_method": "sms",
                    "reason_code": "SELF_SERVICE_VERIFIED",
                    "replaced_binding_count": len(current_bindings),
                },
            )
            issued_box.append(issued)

        try:
            run_owned_transaction(self._session, _persist)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=AuthErrorCode.WECHAT_ALREADY_BOUND,
                message="WeChat identity is already bound; use account merge",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="WeChat rebind could not be completed",
                status_code=500,
            ) from exc
        if not issued_box:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="WeChat rebind could not be completed",
                status_code=500,
            )
        return issued_box[0]
