"""Fixture-only WorkOrder graph; reuses Task 5 employee identities."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.domain.identity import ResidentRelationStatus, ResidentRelationType
from app.domain.ai import (
    AI_ANALYSIS_READ,
    AI_ANALYSIS_REVIEW,
    ANNOUNCEMENT_DRAFT,
    ANNOUNCEMENT_PUBLISH,
    ANNOUNCEMENT_READ,
    DISPATCH_RECOMMENDATION_READ,
    INCIDENT_MANAGE,
    INCIDENT_READ,
)
from app.domain.knowledge import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from app.domain.work_order import (
    DEFAULT_SLA_COMPLETE_MINUTES,
    DEFAULT_SLA_RESPOND_MINUTES,
    WORK_ORDER_CANCEL,
    WORK_ORDER_CREATE,
    WORK_ORDER_DECIDE,
    WORK_ORDER_DISPATCH,
    WORK_ORDER_PROCESS,
    WORK_ORDER_READ,
    WORK_ORDER_RESOLVE,
    WorkOrderCategory,
    WorkOrderPriority,
)
from app.model.identity import Permission, Role, RolePermission, User, UserRole
from app.model.resident import ResidentHouseRelation
from app.model.work_order import SlaPolicy
from app.service.identity.property import PropertyService
from tests.integration.employee_test_data import (
    EmployeeAttendanceGraph,
    create_employee_attendance_graph,
)


@dataclass(frozen=True, slots=True)
class WorkOrderGraph:
    employees: EmployeeAttendanceGraph
    house_1_id: int
    house_2_id: int
    house_3_id: int
    resident_b_id: int
    relation_a_id: int


def create_work_order_graph(session: Session) -> WorkOrderGraph:
    employees = create_employee_attendance_graph(session)
    properties = PropertyService(session)
    building_1 = properties.create_building(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_1_id,
        code="WO-B1",
        name="WO Building 1",
    )
    building_2 = properties.create_building(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_2_id,
        code="WO-B2",
        name="WO Building 2",
    )
    building_3 = properties.create_building(
        tenant_id=employees.tenant_2_id,
        property_id=employees.property_3_id,
        code="WO-B3",
        name="WO Building 3",
    )
    unit_1 = properties.create_unit(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_1_id,
        building_id=building_1.id,
        code="WO-U1",
        name="WO Unit 1",
    )
    unit_2 = properties.create_unit(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_2_id,
        building_id=building_2.id,
        code="WO-U2",
        name="WO Unit 2",
    )
    unit_3 = properties.create_unit(
        tenant_id=employees.tenant_2_id,
        property_id=employees.property_3_id,
        building_id=building_3.id,
        code="WO-U3",
        name="WO Unit 3",
    )
    house_1 = properties.create_house(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_1_id,
        unit_id=unit_1.id,
        room_no="101",
    )
    house_2 = properties.create_house(
        tenant_id=employees.tenant_1_id,
        property_id=employees.property_2_id,
        unit_id=unit_2.id,
        room_no="201",
    )
    house_3 = properties.create_house(
        tenant_id=employees.tenant_2_id,
        property_id=employees.property_3_id,
        unit_id=unit_3.id,
        room_no="301",
    )
    with session.begin():
        resident_b = User(status="ACTIVE", display_name="Resident B")
        session.add(resident_b)
        session.flush()
        roles = {
            role.code: role
            for role in session.scalars(select(Role)).all()
        }
        session.add(
            UserRole(
                user_id=resident_b.id,
                role_id=roles["RESIDENT"].id,
                scope_type="PROPERTY",
                tenant_id=employees.tenant_1_id,
                property_id=employees.property_2_id,
            )
        )
        permission_defs = (
            (WORK_ORDER_CREATE, "Create work orders"),
            (WORK_ORDER_READ, "Read work orders"),
            (WORK_ORDER_DISPATCH, "Dispatch work orders"),
            (WORK_ORDER_PROCESS, "Process work orders"),
            (WORK_ORDER_RESOLVE, "Resolve work orders"),
            (WORK_ORDER_DECIDE, "Decide work orders"),
            (WORK_ORDER_CANCEL, "Cancel work orders"),
            (AI_ANALYSIS_READ, "Read AI analysis"),
            (AI_ANALYSIS_REVIEW, "Review AI analysis"),
            (DISPATCH_RECOMMENDATION_READ, "Read dispatch recommendations"),
            (INCIDENT_READ, "Read incidents"),
            (INCIDENT_MANAGE, "Manage incidents"),
            (ANNOUNCEMENT_READ, "Read announcements"),
            (ANNOUNCEMENT_DRAFT, "Draft announcements"),
            (ANNOUNCEMENT_PUBLISH, "Publish announcements"),
            (KNOWLEDGE_READ, "Read knowledge"),
            (KNOWLEDGE_MANAGE, "Manage knowledge"),
            ("audit:read", "Read audit"),
        )
        permissions: dict[str, Permission] = {}
        for code, name in permission_defs:
            permission = Permission(code=code, name=name)
            session.add(permission)
            permissions[code] = permission
        session.flush()
        grants = (
            ("RESIDENT", (WORK_ORDER_CREATE, WORK_ORDER_READ, WORK_ORDER_CANCEL, ANNOUNCEMENT_READ, KNOWLEDGE_READ)),
            (
                "STAFF",
                (
                    WORK_ORDER_READ,
                    WORK_ORDER_PROCESS,
                    WORK_ORDER_RESOLVE,
                    INCIDENT_READ,
                    ANNOUNCEMENT_READ,
                    KNOWLEDGE_READ,
                ),
            ),
            (
                "SUPERVISOR",
                (
                    WORK_ORDER_READ,
                    WORK_ORDER_DISPATCH,
                    WORK_ORDER_PROCESS,
                    WORK_ORDER_RESOLVE,
                    WORK_ORDER_DECIDE,
                    WORK_ORDER_CANCEL,
                    AI_ANALYSIS_READ,
                    AI_ANALYSIS_REVIEW,
                    DISPATCH_RECOMMENDATION_READ,
                    INCIDENT_READ,
                    INCIDENT_MANAGE,
                    ANNOUNCEMENT_READ,
                    ANNOUNCEMENT_DRAFT,
                    KNOWLEDGE_READ,
                ),
            ),
            (
                "PROPERTY_ADMIN",
                (
                    WORK_ORDER_READ,
                    WORK_ORDER_DISPATCH,
                    WORK_ORDER_PROCESS,
                    WORK_ORDER_RESOLVE,
                    WORK_ORDER_DECIDE,
                    WORK_ORDER_CANCEL,
                    AI_ANALYSIS_READ,
                    AI_ANALYSIS_REVIEW,
                    DISPATCH_RECOMMENDATION_READ,
                    INCIDENT_READ,
                    INCIDENT_MANAGE,
                    ANNOUNCEMENT_READ,
                    ANNOUNCEMENT_DRAFT,
                    ANNOUNCEMENT_PUBLISH,
                    KNOWLEDGE_READ,
                    KNOWLEDGE_MANAGE,
                ),
            ),
            (
                "TENANT_ADMIN",
                (
                    WORK_ORDER_READ,
                    WORK_ORDER_DISPATCH,
                    WORK_ORDER_PROCESS,
                    WORK_ORDER_RESOLVE,
                    WORK_ORDER_DECIDE,
                    WORK_ORDER_CANCEL,
                    AI_ANALYSIS_READ,
                    AI_ANALYSIS_REVIEW,
                    DISPATCH_RECOMMENDATION_READ,
                    INCIDENT_READ,
                    INCIDENT_MANAGE,
                    ANNOUNCEMENT_READ,
                    ANNOUNCEMENT_DRAFT,
                    ANNOUNCEMENT_PUBLISH,
                    KNOWLEDGE_READ,
                    KNOWLEDGE_MANAGE,
                ),
            ),
            (
                "SYSTEM_ADMIN",
                ("audit:read",),
            ),
        )
        for role_code, codes in grants:
            for code in codes:
                session.add(
                    RolePermission(
                        role_id=roles[role_code].id,
                        permission_id=permissions[code].id,
                    )
                )
        now = utc_now()
        relation_a = ResidentHouseRelation(
            user_id=employees.resident_id,
            tenant_id=employees.tenant_1_id,
            property_id=employees.property_1_id,
            house_id=house_1.id,
            relation_type=ResidentRelationType.OWNER.value,
            status=ResidentRelationStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        relation_b = ResidentHouseRelation(
            user_id=resident_b.id,
            tenant_id=employees.tenant_1_id,
            property_id=employees.property_2_id,
            house_id=house_2.id,
            relation_type=ResidentRelationType.OWNER.value,
            status=ResidentRelationStatus.ACTIVE.value,
            valid_from=now,
            valid_to=None,
        )
        session.add_all((relation_a, relation_b))
        for category in WorkOrderCategory:
            for priority in WorkOrderPriority:
                session.add(
                    SlaPolicy(
                        tenant_id=employees.tenant_1_id,
                        property_id=None,
                        category=category.value,
                        priority=priority.value,
                        respond_minutes=DEFAULT_SLA_RESPOND_MINUTES[priority],
                        complete_minutes=DEFAULT_SLA_COMPLETE_MINUTES[priority],
                        status="ACTIVE",
                    )
                )
        session.flush()
        graph = WorkOrderGraph(
            employees=employees,
            house_1_id=house_1.id,
            house_2_id=house_2.id,
            house_3_id=house_3.id,
            resident_b_id=resident_b.id,
            relation_a_id=relation_a.id,
        )
    if session.in_transaction():
        session.rollback()
    return graph
