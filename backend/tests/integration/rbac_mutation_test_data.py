"""Fixture-only RBAC mutation graph builders; not runtime seed code."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.identity import Principal
from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.model.property import Property, Tenant


@dataclass(frozen=True, slots=True)
class RbacMutationGraph:
    tenant_1_id: int
    tenant_2_id: int
    property_1_id: int
    property_2_id: int
    property_3_id: int
    system_actor_id: int
    tenant_actor_id: int
    property_actor_id: int
    disabled_actor_id: int
    target_user_id: int
    target_role_id: int
    target_permission_id: int
    system_actor_grant_id: int


def identity_only_principal(user_id: int) -> Principal:
    return Principal(
        user_id=user_id,
        status="ACTIVE",
        display_name="Caller Snapshot",
        grants=(),
    )


def create_rbac_mutation_graph(session: Session) -> RbacMutationGraph:
    with session.begin():
        tenant_1 = Tenant(name="C2 Tenant 1", status="ACTIVE")
        tenant_2 = Tenant(name="C2 Tenant 2", status="ACTIVE")
        session.add_all((tenant_1, tenant_2))
        session.flush()
        property_1 = Property(
            tenant_id=tenant_1.id,
            name="C2 Property 1",
            address="C2 Address 1",
            timezone="Asia/Shanghai",
        )
        property_2 = Property(
            tenant_id=tenant_1.id,
            name="C2 Property 2",
            address="C2 Address 2",
            timezone="Asia/Shanghai",
        )
        property_3 = Property(
            tenant_id=tenant_2.id,
            name="C2 Property 3",
            address="C2 Address 3",
            timezone="Asia/Shanghai",
        )
        users = {
            "system": User(status="ACTIVE", display_name="C2 System Actor"),
            "tenant": User(status="ACTIVE", display_name="C2 Tenant Actor"),
            "property": User(status="ACTIVE", display_name="C2 Property Actor"),
            "disabled": User(status="DISABLED", display_name="C2 Disabled Actor"),
            "target": User(status="ACTIVE", display_name="C2 Target"),
        }
        roles = {
            "dummy": Role(code="DUMMY_ROLE", name="Dummy Role"),
            "system": Role(code="SYSTEM_ADMIN", name="System Admin"),
            "tenant": Role(code="TENANT_ADMIN", name="Tenant Admin"),
            "property": Role(code="PROPERTY_ADMIN", name="Property Admin"),
            "target": Role(code="STAFF", name="Staff"),
        }
        permissions = {
            "dummy": Permission(code="dummy:read", name="Dummy Read"),
            "manage": Permission(code="role:manage", name="Manage roles"),
            "target": Permission(code="property:read", name="Read property"),
        }
        session.add_all(
            (
                property_1,
                property_2,
                property_3,
                *users.values(),
                *roles.values(),
                *permissions.values(),
            )
        )
        session.flush()
        session.add_all(
            (
                RolePermission(
                    role_id=roles["system"].id,
                    permission_id=permissions["manage"].id,
                ),
                RolePermission(
                    role_id=roles["tenant"].id,
                    permission_id=permissions["manage"].id,
                ),
                RolePermission(
                    role_id=roles["property"].id,
                    permission_id=permissions["manage"].id,
                ),
            )
        )
        system_actor_grant = UserRole(
            user_id=users["system"].id,
            role_id=roles["system"].id,
            scope_type="SYSTEM",
        )
        session.add_all(
            (
                system_actor_grant,
                UserRole(
                    user_id=users["tenant"].id,
                    role_id=roles["tenant"].id,
                    scope_type="TENANT",
                    tenant_id=tenant_1.id,
                ),
                UserRole(
                    user_id=users["property"].id,
                    role_id=roles["property"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["disabled"].id,
                    role_id=roles["system"].id,
                    scope_type="SYSTEM",
                ),
            )
        )
        session.flush()

    return RbacMutationGraph(
        tenant_1_id=tenant_1.id,
        tenant_2_id=tenant_2.id,
        property_1_id=property_1.id,
        property_2_id=property_2.id,
        property_3_id=property_3.id,
        system_actor_id=users["system"].id,
        tenant_actor_id=users["tenant"].id,
        property_actor_id=users["property"].id,
        disabled_actor_id=users["disabled"].id,
        target_user_id=users["target"].id,
        target_role_id=roles["target"].id,
        target_permission_id=permissions["target"].id,
        system_actor_grant_id=system_actor_grant.id,
    )
