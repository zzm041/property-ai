"""Fixture-only resident binding graph; not a Development Seed runtime."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.service.property import PropertyService


@dataclass(frozen=True, slots=True)
class ResidentBindingGraph:
    tenant_1_id: int
    tenant_2_id: int
    property_1_id: int
    property_2_id: int
    property_3_id: int
    house_1_id: int
    house_2_id: int
    house_3_id: int
    resident_a_id: int
    resident_b_id: int
    reviewer_p1_id: int
    reviewer_p2_id: int
    tenant_admin_id: int
    system_admin_id: int
    staff_id: int
    disabled_id: int


def create_resident_binding_graph(session: Session) -> ResidentBindingGraph:
    properties = PropertyService(session)
    tenant_1 = properties.create_tenant(name="T4 Tenant 1")
    tenant_2 = properties.create_tenant(name="T4 Tenant 2")
    property_1 = properties.create_property(
        tenant_id=tenant_1.id,
        name="T4 Property 1",
        address="Address 1",
    )
    property_2 = properties.create_property(
        tenant_id=tenant_1.id,
        name="T4 Property 2",
        address="Address 2",
    )
    property_3 = properties.create_property(
        tenant_id=tenant_2.id,
        name="T4 Property 3",
        address="Address 3",
    )
    building_1 = properties.create_building(
        tenant_id=tenant_1.id,
        property_id=property_1.id,
        code="B1",
        name="Building 1",
    )
    building_2 = properties.create_building(
        tenant_id=tenant_1.id,
        property_id=property_2.id,
        code="B2",
        name="Building 2",
    )
    building_3 = properties.create_building(
        tenant_id=tenant_2.id,
        property_id=property_3.id,
        code="B3",
        name="Building 3",
    )
    unit_1 = properties.create_unit(
        tenant_id=tenant_1.id,
        property_id=property_1.id,
        building_id=building_1.id,
        code="U1",
        name="Unit 1",
    )
    unit_2 = properties.create_unit(
        tenant_id=tenant_1.id,
        property_id=property_2.id,
        building_id=building_2.id,
        code="U2",
        name="Unit 2",
    )
    unit_3 = properties.create_unit(
        tenant_id=tenant_2.id,
        property_id=property_3.id,
        building_id=building_3.id,
        code="U3",
        name="Unit 3",
    )
    house_1 = properties.create_house(
        tenant_id=tenant_1.id,
        property_id=property_1.id,
        unit_id=unit_1.id,
        room_no="101",
    )
    house_2 = properties.create_house(
        tenant_id=tenant_1.id,
        property_id=property_2.id,
        unit_id=unit_2.id,
        room_no="201",
    )
    house_3 = properties.create_house(
        tenant_id=tenant_2.id,
        property_id=property_3.id,
        unit_id=unit_3.id,
        room_no="301",
    )

    with session.begin():
        users = {
            "a": User(status="ACTIVE", display_name="Resident A"),
            "b": User(status="ACTIVE", display_name="Resident B"),
            "p1": User(status="ACTIVE", display_name="Reviewer P1"),
            "p2": User(status="ACTIVE", display_name="Reviewer P2"),
            "tenant": User(status="ACTIVE", display_name="Tenant Admin"),
            "system": User(status="ACTIVE", display_name="System Admin"),
            "staff": User(status="ACTIVE", display_name="Staff"),
            "disabled": User(status="DISABLED", display_name="Disabled"),
        }
        session.add_all(users.values())
        session.flush()
        roles = {
            "RESIDENT": Role(code="RESIDENT", name="Resident"),
            "PROPERTY_ADMIN": Role(code="PROPERTY_ADMIN", name="Property Admin"),
            "TENANT_ADMIN": Role(code="TENANT_ADMIN", name="Tenant Admin"),
            "SYSTEM_ADMIN": Role(code="SYSTEM_ADMIN", name="System Admin"),
            "STAFF": Role(code="STAFF", name="Staff"),
        }
        session.add_all(roles.values())
        session.flush()
        permissions = {
            "create": Permission(
                code="resident_binding:create",
                name="Create bind",
            ),
            "read": Permission(
                code="resident_binding:read",
                name="Read bind",
            ),
            "review": Permission(
                code="resident_binding:review",
                name="Review bind",
            ),
            "relation": Permission(
                code="resident_relation:read",
                name="Read relation",
            ),
        }
        session.add_all(permissions.values())
        session.flush()
        session.add_all(
            (
                RolePermission(
                    role_id=roles["RESIDENT"].id,
                    permission_id=permissions["create"].id,
                ),
                RolePermission(
                    role_id=roles["RESIDENT"].id,
                    permission_id=permissions["read"].id,
                ),
                RolePermission(
                    role_id=roles["RESIDENT"].id,
                    permission_id=permissions["relation"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["review"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["read"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["review"].id,
                ),
            )
        )
        session.add_all(
            (
                UserRole(
                    user_id=users["a"].id,
                    role_id=roles["RESIDENT"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["b"].id,
                    role_id=roles["RESIDENT"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["p1"].id,
                    role_id=roles["PROPERTY_ADMIN"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["p2"].id,
                    role_id=roles["PROPERTY_ADMIN"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_2.id,
                ),
                UserRole(
                    user_id=users["tenant"].id,
                    role_id=roles["TENANT_ADMIN"].id,
                    scope_type="TENANT",
                    tenant_id=tenant_1.id,
                    property_id=None,
                ),
                UserRole(
                    user_id=users["system"].id,
                    role_id=roles["SYSTEM_ADMIN"].id,
                    scope_type="SYSTEM",
                    tenant_id=None,
                    property_id=None,
                ),
                UserRole(
                    user_id=users["staff"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
            )
        )

    graph = ResidentBindingGraph(
        tenant_1_id=tenant_1.id,
        tenant_2_id=tenant_2.id,
        property_1_id=property_1.id,
        property_2_id=property_2.id,
        property_3_id=property_3.id,
        house_1_id=house_1.id,
        house_2_id=house_2.id,
        house_3_id=house_3.id,
        resident_a_id=users["a"].id,
        resident_b_id=users["b"].id,
        reviewer_p1_id=users["p1"].id,
        reviewer_p2_id=users["p2"].id,
        tenant_admin_id=users["tenant"].id,
        system_admin_id=users["system"].id,
        staff_id=users["staff"].id,
        disabled_id=users["disabled"].id,
    )
    if session.in_transaction():
        session.rollback()
    return graph
