"""Persistence operations for audited RBAC relationship mutations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.iam import OrganizationScope
from app.model.identity import Permission, Role, RolePermission, User, UserRole

_BINARY_COLLATION = "utf8mb4_bin"


class RbacMutationRepository:
    """Query, add, delete, and flush only; the Service owns transactions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_user_for_update(self, user_id: int) -> User | None:
        return self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def get_role_by_code(self, role_code: str) -> Role | None:
        return self._session.scalar(
            select(Role).where(
                Role.code.collate(_BINARY_COLLATION) == role_code
            )
        )

    def get_permission_by_code(
        self,
        permission_code: str,
    ) -> Permission | None:
        return self._session.scalar(
            select(Permission).where(
                Permission.code.collate(_BINARY_COLLATION) == permission_code
            )
        )

    def find_user_role(
        self,
        *,
        user_id: int,
        role_id: int,
        scope: OrganizationScope,
    ) -> UserRole | None:
        return self._find_user_role(
            user_id=user_id,
            role_id=role_id,
            scope=scope,
            for_update=False,
        )

    def find_user_role_for_update(
        self,
        *,
        user_id: int,
        role_id: int,
        scope: OrganizationScope,
    ) -> UserRole | None:
        return self._find_user_role(
            user_id=user_id,
            role_id=role_id,
            scope=scope,
            for_update=True,
        )

    def find_role_permission(
        self,
        *,
        role_id: int,
        permission_id: int,
    ) -> RolePermission | None:
        return self._find_role_permission(
            role_id=role_id,
            permission_id=permission_id,
            for_update=False,
        )

    def find_role_permission_for_update(
        self,
        *,
        role_id: int,
        permission_id: int,
    ) -> RolePermission | None:
        return self._find_role_permission(
            role_id=role_id,
            permission_id=permission_id,
            for_update=True,
        )

    def add_user_role(self, user_role: UserRole) -> UserRole:
        self._session.add(user_role)
        self._session.flush()
        return user_role

    def delete_user_role(self, user_role: UserRole) -> None:
        self._session.delete(user_role)
        self._session.flush()

    def add_role_permission(
        self,
        role_permission: RolePermission,
    ) -> RolePermission:
        self._session.add(role_permission)
        self._session.flush()
        return role_permission

    def delete_role_permission(
        self,
        role_permission: RolePermission,
    ) -> None:
        self._session.delete(role_permission)
        self._session.flush()

    def _find_user_role(
        self,
        *,
        user_id: int,
        role_id: int,
        scope: OrganizationScope,
        for_update: bool,
    ) -> UserRole | None:
        statement = select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
            UserRole.scope_type == scope.scope_type.value,
            UserRole.tenant_id == scope.tenant_id,
            UserRole.property_id == scope.property_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def _find_role_permission(
        self,
        *,
        role_id: int,
        permission_id: int,
        for_update: bool,
    ) -> RolePermission | None:
        statement = select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)
