"""Fixture-only IAM graph builders; this is not a Development Seed runtime."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.service.identity.property import PropertyService


@dataclass(frozen=True, slots=True)
class IamTestGraph:
    tenant_1_id: int
    tenant_2_id: int
    property_1_id: int
    property_2_id: int
    property_3_id: int
    manager_user_id: int
    resident_user_id: int
    disabled_user_id: int
    tenant_admin_user_id: int
    system_admin_user_id: int
    multi_role_user_id: int
    manager_grant_id: int
    manager_manage_role_permission_id: int


def create_iam_test_graph(session: Session) -> IamTestGraph:
    property_service = PropertyService(session)
    tenant_1 = property_service.create_tenant(name="Gate B Tenant 1")
    property_1 = property_service.create_property(
        tenant_id=tenant_1.id,
        name="Gate B Property 1",
        address="Address 1",
    )
    property_2 = property_service.create_property(
        tenant_id=tenant_1.id,
        name="Gate B Property 2",
        address="Address 2",
    )
    tenant_2 = property_service.create_tenant(name="Gate B Tenant 2")
    property_3 = property_service.create_property(
        tenant_id=tenant_2.id,
        name="Gate B Property 3",
        address="Address 3",
    )

    with session.begin():
        users = {
            "manager": User(status="ACTIVE", display_name="Manager"),
            "resident": User(status="ACTIVE", display_name="Resident"),
            "disabled": User(status="DISABLED", display_name="Disabled"),
            "tenant_admin": User(status="ACTIVE", display_name="Tenant Admin"),
            "system_admin": User(status="ACTIVE", display_name="System Admin"),
            "multi_role": User(status="ACTIVE", display_name="Multi Role"),
        }
        roles = {
            "PROPERTY_ADMIN": Role(
                code="PROPERTY_ADMIN",
                name="Property Admin",
            ),
            "RESIDENT": Role(code="RESIDENT", name="Resident"),
            "TENANT_ADMIN": Role(code="TENANT_ADMIN", name="Tenant Admin"),
            "SYSTEM_ADMIN": Role(code="SYSTEM_ADMIN", name="System Admin"),
            "STAFF": Role(code="STAFF", name="Staff"),
        }
        permissions = {
            "property:read": Permission(
                code="property:read",
                name="Read property",
            ),
            "property:manage": Permission(
                code="property:manage",
                name="Manage property",
            ),
            "task:read": Permission(code="task:read", name="Read task"),
        }
        session.add_all((*users.values(), *roles.values(), *permissions.values()))
        session.flush()

        role_permissions = {
            "manager_read": RolePermission(
                role_id=roles["PROPERTY_ADMIN"].id,
                permission_id=permissions["property:read"].id,
            ),
            "manager_manage": RolePermission(
                role_id=roles["PROPERTY_ADMIN"].id,
                permission_id=permissions["property:manage"].id,
            ),
            "resident_read": RolePermission(
                role_id=roles["RESIDENT"].id,
                permission_id=permissions["property:read"].id,
            ),
            "tenant_read": RolePermission(
                role_id=roles["TENANT_ADMIN"].id,
                permission_id=permissions["property:read"].id,
            ),
            "system_read": RolePermission(
                role_id=roles["SYSTEM_ADMIN"].id,
                permission_id=permissions["property:read"].id,
            ),
            "system_manage": RolePermission(
                role_id=roles["SYSTEM_ADMIN"].id,
                permission_id=permissions["property:manage"].id,
            ),
            "staff_task": RolePermission(
                role_id=roles["STAFF"].id,
                permission_id=permissions["task:read"].id,
            ),
        }
        session.add_all(role_permissions.values())
        session.flush()

        manager_grant = UserRole(
            user_id=users["manager"].id,
            role_id=roles["PROPERTY_ADMIN"].id,
            scope_type="PROPERTY",
            tenant_id=tenant_1.id,
            property_id=property_1.id,
        )
        grants = (
            manager_grant,
            UserRole(
                user_id=users["resident"].id,
                role_id=roles["RESIDENT"].id,
                scope_type="PROPERTY",
                tenant_id=tenant_1.id,
                property_id=property_1.id,
            ),
            UserRole(
                user_id=users["disabled"].id,
                role_id=roles["RESIDENT"].id,
                scope_type="PROPERTY",
                tenant_id=tenant_1.id,
                property_id=property_1.id,
            ),
            UserRole(
                user_id=users["tenant_admin"].id,
                role_id=roles["TENANT_ADMIN"].id,
                scope_type="TENANT",
                tenant_id=tenant_1.id,
                property_id=None,
            ),
            UserRole(
                user_id=users["system_admin"].id,
                role_id=roles["SYSTEM_ADMIN"].id,
                scope_type="SYSTEM",
                tenant_id=None,
                property_id=None,
            ),
            UserRole(
                user_id=users["multi_role"].id,
                role_id=roles["STAFF"].id,
                scope_type="PROPERTY",
                tenant_id=tenant_1.id,
                property_id=property_1.id,
            ),
            UserRole(
                user_id=users["multi_role"].id,
                role_id=roles["PROPERTY_ADMIN"].id,
                scope_type="PROPERTY",
                tenant_id=tenant_1.id,
                property_id=property_1.id,
            ),
        )
        session.add_all(grants)
        session.flush()

    return IamTestGraph(
        tenant_1_id=tenant_1.id,
        tenant_2_id=tenant_2.id,
        property_1_id=property_1.id,
        property_2_id=property_2.id,
        property_3_id=property_3.id,
        manager_user_id=users["manager"].id,
        resident_user_id=users["resident"].id,
        disabled_user_id=users["disabled"].id,
        tenant_admin_user_id=users["tenant_admin"].id,
        system_admin_user_id=users["system_admin"].id,
        multi_role_user_id=users["multi_role"].id,
        manager_grant_id=manager_grant.id,
        manager_manage_role_permission_id=role_permissions["manager_manage"].id,
    )
