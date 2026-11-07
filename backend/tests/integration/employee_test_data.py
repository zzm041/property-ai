"""Fixture-only employee / attendance graph; not a Development Seed runtime."""

from dataclasses import dataclass

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.employee import EmployeePropertyStatus, EmployeeStatus
from app.model.employee import (
    Department,
    Duty,
    Employee,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Skill,
)
from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.service.property import PropertyService


@dataclass(frozen=True, slots=True)
class EmployeeAttendanceGraph:
    tenant_1_id: int
    tenant_2_id: int
    property_1_id: int
    property_2_id: int
    property_3_id: int
    department_1_id: int
    department_2_id: int
    duty_maintenance_id: int
    duty_security_id: int
    skill_electrical_id: int
    skill_plumbing_id: int
    staff_a_user_id: int
    staff_b_user_id: int
    staff_p2_user_id: int
    staff_t2_user_id: int
    disabled_staff_user_id: int
    employee_a_id: int
    employee_b_id: int
    employee_p2_id: int
    employee_t2_id: int
    employee_disabled_id: int
    assignment_a_p1_id: int
    manager_p1_id: int
    manager_p2_id: int
    supervisor_p1_id: int
    tenant_admin_id: int
    system_admin_id: int
    resident_id: int


def create_employee_attendance_graph(session: Session) -> EmployeeAttendanceGraph:
    properties = PropertyService(session)
    tenant_1 = properties.create_tenant(name="T5 Tenant 1")
    tenant_2 = properties.create_tenant(name="T5 Tenant 2")
    property_1 = properties.create_property(
        tenant_id=tenant_1.id,
        name="T5 Property 1",
        address="Address 1",
        timezone="Asia/Shanghai",
    )
    property_2 = properties.create_property(
        tenant_id=tenant_1.id,
        name="T5 Property 2",
        address="Address 2",
        timezone="Asia/Shanghai",
    )
    property_3 = properties.create_property(
        tenant_id=tenant_2.id,
        name="T5 Property 3",
        address="Address 3",
        timezone="America/New_York",
    )

    with session.begin():
        users = {
            "a": User(status="ACTIVE", display_name="Staff A"),
            "b": User(status="ACTIVE", display_name="Staff B"),
            "p2": User(status="ACTIVE", display_name="Staff P2"),
            "t2": User(status="ACTIVE", display_name="Staff T2"),
            "disabled_staff": User(status="ACTIVE", display_name="Disabled Staff User"),
            "m1": User(status="ACTIVE", display_name="Manager P1"),
            "m2": User(status="ACTIVE", display_name="Manager P2"),
            "sup": User(status="ACTIVE", display_name="Supervisor P1"),
            "tenant": User(status="ACTIVE", display_name="Tenant Admin"),
            "system": User(status="ACTIVE", display_name="System Admin"),
            "resident": User(status="ACTIVE", display_name="Resident"),
        }
        session.add_all(users.values())
        session.flush()
        roles = {
            "STAFF": Role(code="STAFF", name="Staff"),
            "SUPERVISOR": Role(code="SUPERVISOR", name="Supervisor"),
            "PROPERTY_ADMIN": Role(code="PROPERTY_ADMIN", name="Property Admin"),
            "TENANT_ADMIN": Role(code="TENANT_ADMIN", name="Tenant Admin"),
            "SYSTEM_ADMIN": Role(code="SYSTEM_ADMIN", name="System Admin"),
            "RESIDENT": Role(code="RESIDENT", name="Resident"),
        }
        session.add_all(roles.values())
        session.flush()
        permissions = {
            "employee_read": Permission(code="employee:read", name="Read employees"),
            "employee_manage": Permission(
                code="employee:manage",
                name="Manage employees",
            ),
            "check_in": Permission(code="attendance:check_in", name="Check in"),
            "check_out": Permission(code="attendance:check_out", name="Check out"),
            "attendance_read": Permission(code="attendance:read", name="Read attendance"),
        }
        session.add_all(permissions.values())
        session.flush()
        session.add_all(
            (
                RolePermission(
                    role_id=roles["STAFF"].id,
                    permission_id=permissions["check_in"].id,
                ),
                RolePermission(
                    role_id=roles["STAFF"].id,
                    permission_id=permissions["check_out"].id,
                ),
                RolePermission(
                    role_id=roles["SUPERVISOR"].id,
                    permission_id=permissions["employee_read"].id,
                ),
                RolePermission(
                    role_id=roles["SUPERVISOR"].id,
                    permission_id=permissions["check_in"].id,
                ),
                RolePermission(
                    role_id=roles["SUPERVISOR"].id,
                    permission_id=permissions["check_out"].id,
                ),
                RolePermission(
                    role_id=roles["SUPERVISOR"].id,
                    permission_id=permissions["attendance_read"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["employee_read"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["employee_manage"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["check_in"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["check_out"].id,
                ),
                RolePermission(
                    role_id=roles["PROPERTY_ADMIN"].id,
                    permission_id=permissions["attendance_read"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["employee_read"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["employee_manage"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["check_in"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["check_out"].id,
                ),
                RolePermission(
                    role_id=roles["TENANT_ADMIN"].id,
                    permission_id=permissions["attendance_read"].id,
                ),
            )
        )
        session.add_all(
            (
                UserRole(
                    user_id=users["a"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["b"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["p2"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_2.id,
                ),
                UserRole(
                    user_id=users["t2"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_2.id,
                    property_id=property_3.id,
                ),
                UserRole(
                    user_id=users["disabled_staff"].id,
                    role_id=roles["STAFF"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["m1"].id,
                    role_id=roles["PROPERTY_ADMIN"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
                UserRole(
                    user_id=users["m2"].id,
                    role_id=roles["PROPERTY_ADMIN"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_2.id,
                ),
                UserRole(
                    user_id=users["sup"].id,
                    role_id=roles["SUPERVISOR"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
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
                    user_id=users["resident"].id,
                    role_id=roles["RESIDENT"].id,
                    scope_type="PROPERTY",
                    tenant_id=tenant_1.id,
                    property_id=property_1.id,
                ),
            )
        )
        department_1 = Department(
            tenant_id=tenant_1.id,
            code="MAINTENANCE",
            name="Maintenance",
            status="ACTIVE",
        )
        department_2 = Department(
            tenant_id=tenant_2.id,
            code="MAINTENANCE",
            name="Maintenance T2",
            status="ACTIVE",
        )
        session.add_all((department_1, department_2))
        session.flush()
        duty_maintenance = Duty(
            tenant_id=tenant_1.id,
            code="MAINTENANCE",
            name="Maintenance",
            status="ACTIVE",
        )
        duty_security = Duty(
            tenant_id=tenant_1.id,
            code="SECURITY",
            name="Security",
            status="ACTIVE",
        )
        duty_t2 = Duty(
            tenant_id=tenant_2.id,
            code="MAINTENANCE",
            name="Maintenance T2",
            status="ACTIVE",
        )
        skill_electrical = Skill(
            tenant_id=tenant_1.id,
            code="ELECTRICAL",
            name="Electrical",
            status="ACTIVE",
        )
        skill_plumbing = Skill(
            tenant_id=tenant_1.id,
            code="PLUMBING",
            name="Plumbing",
            status="ACTIVE",
        )
        skill_t2 = Skill(
            tenant_id=tenant_2.id,
            code="ELECTRICAL",
            name="Electrical T2",
            status="ACTIVE",
        )
        session.add_all(
            (
                duty_maintenance,
                duty_security,
                duty_t2,
                skill_electrical,
                skill_plumbing,
                skill_t2,
            )
        )
        session.flush()
        now = datetime(2020, 1, 1, tzinfo=UTC)
        employee_a = Employee(
            user_id=users["a"].id,
            tenant_id=tenant_1.id,
            employee_no="E-A",
            department_id=department_1.id,
            status=EmployeeStatus.ACTIVE.value,
            max_capacity=1,
        )
        employee_b = Employee(
            user_id=users["b"].id,
            tenant_id=tenant_1.id,
            employee_no="E-B",
            department_id=department_1.id,
            status=EmployeeStatus.ACTIVE.value,
            max_capacity=1,
        )
        employee_p2 = Employee(
            user_id=users["p2"].id,
            tenant_id=tenant_1.id,
            employee_no="E-P2",
            department_id=department_1.id,
            status=EmployeeStatus.ACTIVE.value,
            max_capacity=1,
        )
        employee_t2 = Employee(
            user_id=users["t2"].id,
            tenant_id=tenant_2.id,
            employee_no="E-T2",
            department_id=department_2.id,
            status=EmployeeStatus.ACTIVE.value,
            max_capacity=1,
        )
        employee_disabled = Employee(
            user_id=users["disabled_staff"].id,
            tenant_id=tenant_1.id,
            employee_no="E-DIS",
            department_id=department_1.id,
            status=EmployeeStatus.DISABLED.value,
            max_capacity=1,
        )
        session.add_all(
            (
                employee_a,
                employee_b,
                employee_p2,
                employee_t2,
                employee_disabled,
            )
        )
        session.flush()
        assignment_a = EmployeeProperty(
            employee_id=employee_a.id,
            tenant_id=tenant_1.id,
            property_id=property_1.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        assignment_b = EmployeeProperty(
            employee_id=employee_b.id,
            tenant_id=tenant_1.id,
            property_id=property_1.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        assignment_p2 = EmployeeProperty(
            employee_id=employee_p2.id,
            tenant_id=tenant_1.id,
            property_id=property_2.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        assignment_t2 = EmployeeProperty(
            employee_id=employee_t2.id,
            tenant_id=tenant_2.id,
            property_id=property_3.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        assignment_disabled = EmployeeProperty(
            employee_id=employee_disabled.id,
            tenant_id=tenant_1.id,
            property_id=property_1.id,
            status=EmployeePropertyStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        session.add_all(
            (
                assignment_a,
                assignment_b,
                assignment_p2,
                assignment_t2,
                assignment_disabled,
            )
        )
        session.add_all(
            (
                EmployeeDuty(
                    employee_id=employee_a.id,
                    tenant_id=tenant_1.id,
                    duty_id=duty_maintenance.id,
                ),
                EmployeeSkill(
                    employee_id=employee_a.id,
                    tenant_id=tenant_1.id,
                    skill_id=skill_electrical.id,
                ),
            )
        )
        session.flush()
        graph = EmployeeAttendanceGraph(
            tenant_1_id=tenant_1.id,
            tenant_2_id=tenant_2.id,
            property_1_id=property_1.id,
            property_2_id=property_2.id,
            property_3_id=property_3.id,
            department_1_id=department_1.id,
            department_2_id=department_2.id,
            duty_maintenance_id=duty_maintenance.id,
            duty_security_id=duty_security.id,
            skill_electrical_id=skill_electrical.id,
            skill_plumbing_id=skill_plumbing.id,
            staff_a_user_id=users["a"].id,
            staff_b_user_id=users["b"].id,
            staff_p2_user_id=users["p2"].id,
            staff_t2_user_id=users["t2"].id,
            disabled_staff_user_id=users["disabled_staff"].id,
            employee_a_id=employee_a.id,
            employee_b_id=employee_b.id,
            employee_p2_id=employee_p2.id,
            employee_t2_id=employee_t2.id,
            employee_disabled_id=employee_disabled.id,
            assignment_a_p1_id=assignment_a.id,
            manager_p1_id=users["m1"].id,
            manager_p2_id=users["m2"].id,
            supervisor_p1_id=users["sup"].id,
            tenant_admin_id=users["tenant"].id,
            system_admin_id=users["system"].id,
            resident_id=users["resident"].id,
        )

    if session.in_transaction():
        session.rollback()
    return graph
