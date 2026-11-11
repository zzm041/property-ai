"""Exact business-key lookups and inserts for Development Seed."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.identity import (
    Permission,
    Role,
    RolePermission,
    User,
    UserPassword,
    UserPhone,
    UserRole,
)
from app.model.property import Property, Tenant

_BINARY_COLLATION = "utf8mb4_bin"


@dataclass(frozen=True, slots=True)
class SeedUserGrantRecord:
    role_code: str
    scope_type: str
    tenant_id: int | None
    property_id: int | None


class DevSeedRepository:
    """Development-only exact lookups and inserts; never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_tenants_by_name_exact(self, name: str) -> tuple[Tenant, ...]:
        return tuple(
            self._session.scalars(
                select(Tenant)
                .where(Tenant.name.collate(_BINARY_COLLATION) == name)
                .order_by(Tenant.id)
                .limit(2)
            ).all()
        )

    def list_properties_by_name_exact(
        self,
        *,
        tenant_id: int,
        name: str,
    ) -> tuple[Property, ...]:
        return tuple(
            self._session.scalars(
                select(Property)
                .where(
                    Property.tenant_id == tenant_id,
                    Property.name.collate(_BINARY_COLLATION) == name,
                )
                .order_by(Property.id)
                .limit(2)
            ).all()
        )

    def list_roles_by_code_exact(self, code: str) -> tuple[Role, ...]:
        return tuple(
            self._session.scalars(
                select(Role)
                .where(Role.code.collate(_BINARY_COLLATION) == code)
                .order_by(Role.id)
                .limit(2)
            ).all()
        )

    def list_permissions_by_code_exact(self, code: str) -> tuple[Permission, ...]:
        return tuple(
            self._session.scalars(
                select(Permission)
                .where(Permission.code.collate(_BINARY_COLLATION) == code)
                .order_by(Permission.id)
                .limit(2)
            ).all()
        )

    def find_role_permission(
        self,
        *,
        role_id: int,
        permission_id: int,
    ) -> RolePermission | None:
        return self._session.scalar(
            select(RolePermission).where(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission_id,
            )
        )

    def list_users_by_display_name_exact(
        self,
        display_name: str,
    ) -> tuple[User, ...]:
        return tuple(
            self._session.scalars(
                select(User)
                .where(
                    User.display_name.collate(_BINARY_COLLATION)
                    == display_name
                )
                .order_by(User.id)
                .limit(2)
            ).all()
        )

    def list_user_grants(
        self,
        user_id: int,
    ) -> tuple[SeedUserGrantRecord, ...]:
        statement = (
            select(
                Role.code.label("role_code"),
                UserRole.scope_type,
                UserRole.tenant_id,
                UserRole.property_id,
            )
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id)
            .order_by(UserRole.id)
        )
        return tuple(
            SeedUserGrantRecord(
                role_code=row.role_code,
                scope_type=row.scope_type,
                tenant_id=row.tenant_id,
                property_id=row.property_id,
            )
            for row in self._session.execute(statement)
        )

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        return self._session.get(Tenant, tenant_id)

    def get_property(self, property_id: int) -> Property | None:
        return self._session.get(Property, property_id)

    def add_tenant(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        self._session.flush()
        return tenant

    def add_property(self, property_record: Property) -> Property:
        self._session.add(property_record)
        self._session.flush()
        return property_record

    def add_role(self, role: Role) -> Role:
        self._session.add(role)
        self._session.flush()
        return role

    def add_permission(self, permission: Permission) -> Permission:
        self._session.add(permission)
        self._session.flush()
        return permission

    def add_user(self, user: User) -> User:
        self._session.add(user)
        self._session.flush()
        return user

    # PA-196: exact lookups for the demo login the seed maintains, so a second run
    # verifies instead of inserting.

    def get_active_phone(self, user_id: int) -> UserPhone | None:
        return self._session.scalars(
            select(UserPhone)
            .where(
                UserPhone.user_id == user_id,
                UserPhone.status == "ACTIVE",
            )
            .order_by(UserPhone.id)
            .limit(1)
        ).first()

    def find_phone_owner(self, phone_e164: str) -> UserPhone | None:
        return self._session.scalars(
            select(UserPhone)
            .where(
                UserPhone.phone_e164.collate(_BINARY_COLLATION) == phone_e164,
                UserPhone.status == "ACTIVE",
            )
            .order_by(UserPhone.id)
            .limit(1)
        ).first()

    def get_password(self, user_id: int) -> UserPassword | None:
        return self._session.scalars(
            select(UserPassword)
            .where(UserPassword.user_id == user_id)
            .order_by(UserPassword.id)
            .limit(1)
        ).first()

    def add_phone(self, phone: UserPhone) -> UserPhone:
        self._session.add(phone)
        self._session.flush()
        return phone

    def add_password(self, password: UserPassword) -> UserPassword:
        self._session.add(password)
        self._session.flush()
        return password
