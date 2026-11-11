"""Development authentication and real-time Principal construction."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import run_owned_transaction
from app.core.errors import AppError, AuthErrorCode, IamErrorCode
from app.core.security import (
    AccessTokenExpiredError,
    InvalidAccessTokenError,
    decode_access_token,
    decode_development_access_token,
    issue_development_access_token,
)
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.dev_seed import SEED_USERS
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType

_ALLOWED_DEV_SEED_KEYS = frozenset(item.display_name for item in SEED_USERS)
from app.repository.identity import GrantPermissionRecord, IdentityRepository
from app.service.audit import AuditService


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    access_token: str
    token_type: str
    expires_in: int


class AuthService:
    """Read identity state and issue/verify development-only access tokens."""

    def __init__(
        self,
        session: Session,
        repository: IdentityRepository | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or IdentityRepository(session)
        self._audit_service = audit_service or AuditService(session)

    def issue_development_token(
        self,
        user_id: int | None,
        settings: Settings,
        *,
        request_id: str,
        seed_key: str | None = None,
    ) -> IssuedAccessToken:
        if (
            not settings.dev_auth_enabled
            or settings.app_env not in {"development", "test"}
        ):
            raise AppError(
                code=AuthErrorCode.DEV_AUTH_DISABLED,
                message="Development Auth is unavailable",
                status_code=404,
            )
        issued: IssuedAccessToken | None = None

        def _persist() -> None:
            nonlocal issued
            user = self._resolve_development_user(user_id=user_id, seed_key=seed_key)
            if user is None:
                raise AppError(
                    code=AuthErrorCode.INVALID_USER,
                    message="Development user is invalid",
                    status_code=401,
                )
            self._require_active(user.status)
            token = issue_development_access_token(user.id, settings)
            self._audit_service.append(
                action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
                resource_type=AuditResourceType.USER,
                actor_user_id=user.id,
                resource_id=user.id,
                request_id=request_id,
                scope_type=ScopeType.SYSTEM,
                tenant_id=None,
                property_id=None,
                details={
                    "auth_method": "development",
                    "token_type": "access",
                },
            )
            issued = IssuedAccessToken(
                access_token=token,
                token_type="bearer",
                expires_in=settings.jwt_access_token_ttl_seconds,
            )

        run_owned_transaction(self._session, _persist)
        if issued is None:
            raise AppError(
                code=AuthErrorCode.INVALID_TOKEN,
                message="Development token could not be issued",
                status_code=500,
            )
        return issued

    def _resolve_development_user(
        self,
        *,
        user_id: int | None,
        seed_key: str | None,
    ):
        if seed_key:
            if seed_key not in _ALLOWED_DEV_SEED_KEYS:
                return None
            return self._repository.get_user_by_display_name(seed_key)
        if user_id is None:
            return None
        return self._repository.get_user(user_id)

    def authenticate_access_token(
        self,
        token: str,
        settings: Settings,
    ) -> Principal:
        try:
            claims = decode_access_token(token, settings)
        except AccessTokenExpiredError as exc:
            raise AppError(
                code=AuthErrorCode.TOKEN_EXPIRED,
                message="Access token has expired",
                status_code=401,
            ) from exc
        except InvalidAccessTokenError as exc:
            raise AppError(
                code=AuthErrorCode.INVALID_TOKEN,
                message="Access token is invalid",
                status_code=401,
            ) from exc
        if claims.auth_method in {"sms", "password", "wechat"} and claims.session_id:
            from app.service.identity.credential_auth import CredentialAuthService

            CredentialAuthService(self._session, settings=settings).require_active_session(
                claims.session_id,
                claims.user_id,
            )
        return self.load_principal(
            claims.user_id,
            missing_user_code=AuthErrorCode.INVALID_TOKEN,
            auth_method=claims.auth_method,
        )

    def load_principal(
        self,
        user_id: int,
        *,
        missing_user_code: str = AuthErrorCode.INVALID_USER,
        auth_method: str = "unknown",
    ) -> Principal:
        user = self._repository.get_user(user_id)
        if user is None:
            raise AppError(
                code=missing_user_code,
                message="Authenticated user is invalid",
                status_code=401,
            )
        self._require_active(user.status)
        records = self._repository.list_grant_permissions(user.id)
        return Principal(
            user_id=user.id,
            status=user.status,
            display_name=user.display_name,
            grants=self._build_grants(records),
            auth_method=auth_method,
        )

    @staticmethod
    def _require_active(status: str) -> None:
        if status != "ACTIVE":
            raise AppError(
                code=AuthErrorCode.USER_DISABLED,
                message="User is not active",
                status_code=401,
            )

    @staticmethod
    def _build_grants(
        records: tuple[GrantPermissionRecord, ...],
    ) -> tuple[RoleGrant, ...]:
        grouped: dict[int, tuple[str, OrganizationScope, set[str]]] = {}
        try:
            for record in records:
                existing = grouped.get(record.grant_id)
                if existing is None:
                    scope = OrganizationScope(
                        scope_type=ScopeType(record.scope_type),
                        tenant_id=record.tenant_id,
                        property_id=record.property_id,
                    )
                    existing = (record.role_code, scope, set())
                    grouped[record.grant_id] = existing
                if record.permission_code is not None:
                    existing[2].add(record.permission_code)
        except ValueError as exc:
            raise AppError(
                code=IamErrorCode.INVALID_SCOPE,
                message="Stored authorization scope is invalid",
                status_code=403,
            ) from exc

        return tuple(
            RoleGrant(
                role_code=role_code,
                scope=scope,
                permissions=frozenset(permissions),
            )
            for _, (role_code, scope, permissions) in sorted(grouped.items())
        )
