"""Development-only Employee / catalog / assignment fixture. Not IAM Seed."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.core.time import utc_now
from app.domain.dev import DEV_PROPERTY_NAME, DEV_TENANT_NAME
from app.domain.work_order import (
    DEV_DUTY_CODES,
    DEV_SKILL_CODES,
    EmployeePropertyStatus,
    EmployeeStatus,
)
from app.model.employee import (
    Department,
    Duty,
    Employee,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Skill,
)
from app.model.identity import User
from app.model.property import Property, Tenant
from app.service.dev_seed import require_dev_seed_environment

DEV_STAFF_DISPLAY_NAME = "__DEV_STAFF__"
DEV_DEPARTMENT_CODE = "__DEV_DEPT__"
DEV_DEPARTMENT_NAME = "Development Only Department"
DEV_EMPLOYEE_NO = "DEV-STAFF-001"
_BINARY_COLLATION = "utf8mb4_bin"


@dataclass(frozen=True, slots=True)
class DevEmployeeFixtureResult:
    tenant_id: int
    property_id: int
    department_id: int
    duty_id: int
    skill_id: int
    user_id: int
    employee_id: int
    assignment_id: int


def run_development_employee_fixture(
    settings: Settings,
    *,
    engine_factory=None,
) -> DevEmployeeFixtureResult:
    require_dev_seed_environment(settings)
    owned_engine = engine_factory is None
    factory = engine_factory or create_database_engine
    engine = factory(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        return _ensure_dev_employee(session)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code=DevSeedErrorCode.FAILED,
            message="Development employee fixture failed",
            status_code=500,
        ) from exc
    finally:
        session.close()
        if owned_engine:
            engine.dispose()


def _ensure_dev_employee(session: Session) -> DevEmployeeFixtureResult:
    if session.in_transaction():
        session.rollback()
    with session.begin():
        tenant = session.scalar(
            select(Tenant)
            .where(Tenant.name.collate(_BINARY_COLLATION) == DEV_TENANT_NAME)
            .order_by(Tenant.id)
            .limit(1)
        )
        if tenant is None:
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development tenant is missing; run IAM seed first",
                status_code=409,
            )
        property_record = session.scalar(
            select(Property)
            .where(
                Property.tenant_id == tenant.id,
                Property.name.collate(_BINARY_COLLATION) == DEV_PROPERTY_NAME,
            )
            .order_by(Property.id)
            .limit(1)
        )
        if property_record is None:
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development property is missing; run IAM seed first",
                status_code=409,
            )
        staff = session.scalar(
            select(User)
            .where(User.display_name.collate(_BINARY_COLLATION) == DEV_STAFF_DISPLAY_NAME)
            .order_by(User.id)
            .limit(1)
        )
        if staff is None or staff.status != "ACTIVE":
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development staff user is missing; run IAM seed first",
                status_code=409,
            )
        department = session.scalar(
            select(Department).where(
                Department.tenant_id == tenant.id,
                Department.code.collate(_BINARY_COLLATION) == DEV_DEPARTMENT_CODE,
            )
        )
        if department is None:
            department = Department(
                tenant_id=tenant.id,
                property_id=None,
                code=DEV_DEPARTMENT_CODE,
                name=DEV_DEPARTMENT_NAME,
                status="ACTIVE",
            )
            session.add(department)
            session.flush()
        duty_ids: dict[str, int] = {}
        for code, name in DEV_DUTY_CODES:
            duty = session.scalar(
                select(Duty).where(
                    Duty.tenant_id == tenant.id,
                    Duty.code.collate(_BINARY_COLLATION) == code,
                )
            )
            if duty is None:
                duty = Duty(
                    tenant_id=tenant.id,
                    code=code,
                    name=name,
                    status="ACTIVE",
                )
                session.add(duty)
                session.flush()
            duty_ids[code] = duty.id
        skill_ids: dict[str, int] = {}
        for code, name in DEV_SKILL_CODES:
            skill = session.scalar(
                select(Skill).where(
                    Skill.tenant_id == tenant.id,
                    Skill.code.collate(_BINARY_COLLATION) == code,
                )
            )
            if skill is None:
                skill = Skill(
                    tenant_id=tenant.id,
                    code=code,
                    name=name,
                    status="ACTIVE",
                )
                session.add(skill)
                session.flush()
            skill_ids[code] = skill.id
        employee = session.scalar(
            select(Employee).where(Employee.user_id == staff.id)
        )
        if employee is None:
            employee = Employee(
                user_id=staff.id,
                tenant_id=tenant.id,
                employee_no=DEV_EMPLOYEE_NO,
                department_id=department.id,
                status=EmployeeStatus.ACTIVE.value,
                max_capacity=1,
            )
            session.add(employee)
            session.flush()
        elif (
            employee.tenant_id != tenant.id
            or employee.employee_no != DEV_EMPLOYEE_NO
        ):
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development employee identity conflicted",
                status_code=409,
            )
        maintenance_duty_id = duty_ids["MAINTENANCE"]
        electrical_skill_id = skill_ids["ELECTRICAL"]
        duty_link = session.scalar(
            select(EmployeeDuty).where(
                EmployeeDuty.employee_id == employee.id,
                EmployeeDuty.duty_id == maintenance_duty_id,
            )
        )
        if duty_link is None:
            session.add(
                EmployeeDuty(
                    employee_id=employee.id,
                    tenant_id=employee.tenant_id,
                    duty_id=maintenance_duty_id,
                )
            )
            session.flush()
        skill_link = session.scalar(
            select(EmployeeSkill).where(
                EmployeeSkill.employee_id == employee.id,
                EmployeeSkill.skill_id == electrical_skill_id,
            )
        )
        if skill_link is None:
            session.add(
                EmployeeSkill(
                    employee_id=employee.id,
                    tenant_id=employee.tenant_id,
                    skill_id=electrical_skill_id,
                )
            )
            session.flush()
        assignment = session.scalar(
            select(EmployeeProperty).where(
                EmployeeProperty.employee_id == employee.id,
                EmployeeProperty.property_id == property_record.id,
                EmployeeProperty.status == EmployeePropertyStatus.ACTIVE.value,
                EmployeeProperty.valid_to.is_(None),
            )
        )
        if assignment is None:
            assignment = EmployeeProperty(
                employee_id=employee.id,
                tenant_id=employee.tenant_id,
                property_id=property_record.id,
                status=EmployeePropertyStatus.ACTIVE.value,
                valid_from=utc_now(),
                valid_to=None,
            )
            session.add(assignment)
            session.flush()
        return DevEmployeeFixtureResult(
            tenant_id=tenant.id,
            property_id=property_record.id,
            department_id=department.id,
            duty_id=maintenance_duty_id,
            skill_id=electrical_skill_id,
            user_id=staff.id,
            employee_id=employee.id,
            assignment_id=assignment.id,
        )
