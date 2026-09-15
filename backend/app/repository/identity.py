"""Read-only identity and RBAC queries for request authentication."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.identity import Permission, Role, RolePermission, User, UserRole


@dataclass(frozen=True, slots=True)
class GrantPermissionRecord:
    grant_id: int
    role_code: str
    scope_type: str
    tenant_id: int | None
    property_id: int | None
    permission_code: str | None


class IdentityRepository:
    """Query-only Gate B repository; never commits or mutates grants."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def list_grant_permissions(
        self,
        user_id: int,
    ) -> tuple[GrantPermissionRecord, ...]:
        statement = (
            select(
                UserRole.id,
                Role.code.label("role_code"),
                UserRole.scope_type,
                UserRole.tenant_id,
                UserRole.property_id,
                Permission.code.label("permission_code"),
            )
            .join(Role, Role.id == UserRole.role_id)
            .outerjoin(
                RolePermission,
                RolePermission.role_id == Role.id,
            )
            .outerjoin(
                Permission,
                Permission.id == RolePermission.permission_id,
            )
            .where(UserRole.user_id == user_id)
            .order_by(UserRole.id, Permission.code)
        )
        return tuple(
            GrantPermissionRecord(
                grant_id=row.id,
                role_code=row.role_code,
                scope_type=row.scope_type,
                tenant_id=row.tenant_id,
                property_id=row.property_id,
                permission_code=row.permission_code,
            )
            for row in self._session.execute(statement)
        )
