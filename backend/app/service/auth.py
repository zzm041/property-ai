"""Development authentication and real-time Principal construction."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode, IamErrorCode
from app.core.security import (
    AccessTokenExpiredError,
    InvalidAccessTokenError,
    decode_development_access_token,
    issue_development_access_token,
)
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.repository.identity import GrantPermissionRecord, IdentityRepository


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
    ) -> None:
        self._repository = repository or IdentityRepository(session)

    def issue_development_token(
        self,
        user_id: int,
        settings: Settings,
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
        user = self._repository.get_user(user_id)
        if user is None:
            raise AppError(
                code=AuthErrorCode.INVALID_USER,
                message="Development user is invalid",
                status_code=401,
            )
        self._require_active(user.status)
        token = issue_development_access_token(user.id, settings)
        return IssuedAccessToken(
            access_token=token,
            token_type="bearer",
            expires_in=settings.jwt_access_token_ttl_seconds,
        )

    def authenticate_access_token(
        self,
        token: str,
        settings: Settings,
    ) -> Principal:
        try:
            claims = decode_development_access_token(token, settings)
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
        return self.load_principal(
            claims.user_id,
            missing_user_code=AuthErrorCode.INVALID_TOKEN,
        )

    def load_principal(
        self,
        user_id: int,
        *,
        missing_user_code: str = AuthErrorCode.INVALID_USER,
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
