"""Scoped persistence for employees, assignments, and attendance."""

from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.model.employee import (
    AttendanceRecord,
    Department,
    Duty,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Skill,
)
from app.model.property import Property


class EmployeeRepository:
    """Query/add/flush only. Transaction commit belongs to Service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_employee(self, employee_id: int) -> Employee | None:
        return self._session.get(Employee, employee_id)

    def get_employee_for_update(self, employee_id: int) -> Employee | None:
        return self._session.scalar(
            select(Employee).where(Employee.id == employee_id).with_for_update()
        )

    def get_employee_in_tenant(
        self,
        *,
        tenant_id: int,
        employee_id: int,
    ) -> Employee | None:
        return self._session.scalar(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.tenant_id == tenant_id,
            )
        )

    def get_employee_for_user(self, user_id: int) -> Employee | None:
        return self._session.scalar(
            select(Employee).where(Employee.user_id == user_id)
        )

    def get_department_in_tenant(
        self,
        *,
        tenant_id: int,
        department_id: int,
    ) -> Department | None:
        return self._session.scalar(
            select(Department).where(
                Department.id == department_id,
                Department.tenant_id == tenant_id,
            )
        )

    def get_duty_in_tenant(self, *, tenant_id: int, duty_id: int) -> Duty | None:
        return self._session.scalar(
            select(Duty).where(Duty.id == duty_id, Duty.tenant_id == tenant_id)
        )

    def get_skill_in_tenant(
        self,
        *,
        tenant_id: int,
        skill_id: int,
    ) -> Skill | None:
        return self._session.scalar(
            select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id)
        )

    def get_department_by_code(
        self,
        *,
        tenant_id: int,
        code: str,
    ) -> Department | None:
        return self._session.scalar(
            select(Department).where(
                Department.tenant_id == tenant_id,
                Department.code == code,
            )
        )

    def get_duty_by_code(self, *, tenant_id: int, code: str) -> Duty | None:
        return self._session.scalar(
            select(Duty).where(Duty.tenant_id == tenant_id, Duty.code == code)
        )

    def get_skill_by_code(self, *, tenant_id: int, code: str) -> Skill | None:
        return self._session.scalar(
            select(Skill).where(Skill.tenant_id == tenant_id, Skill.code == code)
        )

    def get_property_in_tenant(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> Property | None:
        return self._session.scalar(
            select(Property).where(
                Property.id == property_id,
                Property.tenant_id == tenant_id,
            )
        )

    def find_effective_assignment(
        self,
        *,
        employee_id: int,
        property_id: int,
        now: datetime,
    ) -> EmployeeProperty | None:
        return self._session.scalar(
            select(EmployeeProperty).where(
                EmployeeProperty.employee_id == employee_id,
                EmployeeProperty.property_id == property_id,
                EmployeeProperty.status == "ACTIVE",
                EmployeeProperty.valid_from <= now,
                or_(
                    EmployeeProperty.valid_to.is_(None),
                    EmployeeProperty.valid_to > now,
                ),
            )
        )

    def list_active_property_ids_for_employee(
        self,
        *,
        employee_id: int,
        now: datetime,
    ) -> tuple[int, ...]:
        rows = self._session.scalars(
            select(EmployeeProperty.property_id).where(
                EmployeeProperty.employee_id == employee_id,
                EmployeeProperty.status == "ACTIVE",
                EmployeeProperty.valid_from <= now,
                or_(
                    EmployeeProperty.valid_to.is_(None),
                    EmployeeProperty.valid_to > now,
                ),
            )
        ).all()
        return tuple(int(item) for item in rows)

    def get_assignment_for_update(
        self,
        assignment_id: int,
    ) -> EmployeeProperty | None:
        return self._session.scalar(
            select(EmployeeProperty)
            .where(EmployeeProperty.id == assignment_id)
            .with_for_update()
        )

    def list_assignments_for_employee(
        self,
        employee_id: int,
    ) -> tuple[EmployeeProperty, ...]:
        return tuple(
            self._session.scalars(
                select(EmployeeProperty)
                .where(EmployeeProperty.employee_id == employee_id)
                .order_by(EmployeeProperty.id)
            ).all()
        )

    def list_duties_for_employee(self, employee_id: int) -> tuple[Duty, ...]:
        return tuple(
            self._session.scalars(
                select(Duty)
                .join(
                    EmployeeDuty,
                    (EmployeeDuty.duty_id == Duty.id)
                    & (EmployeeDuty.tenant_id == Duty.tenant_id),
                )
                .where(EmployeeDuty.employee_id == employee_id)
                .order_by(Duty.id)
            ).all()
        )

    def list_skills_for_employee(self, employee_id: int) -> tuple[Skill, ...]:
        return tuple(
            self._session.scalars(
                select(Skill)
                .join(
                    EmployeeSkill,
                    (EmployeeSkill.skill_id == Skill.id)
                    & (EmployeeSkill.tenant_id == Skill.tenant_id),
                )
                .where(EmployeeSkill.employee_id == employee_id)
                .order_by(Skill.id)
            ).all()
        )

    def get_attendance_for_update(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date: date,
    ) -> AttendanceRecord | None:
        return self._session.scalar(
            select(AttendanceRecord)
            .where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.property_id == property_id,
                AttendanceRecord.business_date == business_date,
            )
            .with_for_update()
        )

    def get_daily_status_for_update(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date: date,
    ) -> EmployeeDailyStatus | None:
        return self._session.scalar(
            select(EmployeeDailyStatus)
            .where(
                EmployeeDailyStatus.employee_id == employee_id,
                EmployeeDailyStatus.property_id == property_id,
                EmployeeDailyStatus.business_date == business_date,
            )
            .with_for_update()
        )

    def get_daily_status(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date: date,
    ) -> EmployeeDailyStatus | None:
        return self._session.scalar(
            select(EmployeeDailyStatus).where(
                EmployeeDailyStatus.employee_id == employee_id,
                EmployeeDailyStatus.property_id == property_id,
                EmployeeDailyStatus.business_date == business_date,
            )
        )

    def get_attendance(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date: date,
    ) -> AttendanceRecord | None:
        return self._session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.property_id == property_id,
                AttendanceRecord.business_date == business_date,
            )
        )

    def list_daily_statuses_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
        business_date: date,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[EmployeeDailyStatus, ...]:
        return tuple(
            self._session.scalars(
                select(EmployeeDailyStatus)
                .where(
                    EmployeeDailyStatus.tenant_id == tenant_id,
                    EmployeeDailyStatus.property_id == property_id,
                    EmployeeDailyStatus.business_date == business_date,
                    EmployeeDailyStatus.id > after_id,
                )
                .order_by(EmployeeDailyStatus.id)
                .limit(limit)
            ).all()
        )

    def add_department(self, department: Department) -> Department:
        self._session.add(department)
        self._session.flush()
        return department

    def add_duty(self, duty: Duty) -> Duty:
        self._session.add(duty)
        self._session.flush()
        return duty

    def add_skill(self, skill: Skill) -> Skill:
        self._session.add(skill)
        self._session.flush()
        return skill

    def add_employee(self, employee: Employee) -> Employee:
        self._session.add(employee)
        self._session.flush()
        return employee

    def add_assignment(self, assignment: EmployeeProperty) -> EmployeeProperty:
        self._session.add(assignment)
        self._session.flush()
        return assignment

    def add_employee_duty(self, link: EmployeeDuty) -> EmployeeDuty:
        self._session.add(link)
        self._session.flush()
        return link

    def add_employee_skill(self, link: EmployeeSkill) -> EmployeeSkill:
        self._session.add(link)
        self._session.flush()
        return link

    def add_attendance(self, record: AttendanceRecord) -> AttendanceRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def add_daily_status(self, status: EmployeeDailyStatus) -> EmployeeDailyStatus:
        self._session.add(status)
        self._session.flush()
        return status

    def end_assignment(
        self,
        assignment: EmployeeProperty,
        *,
        ended_at: datetime,
        status: str,
    ) -> EmployeeProperty:
        assignment.valid_to = ended_at
        assignment.status = status
        self._session.flush()
        return assignment
