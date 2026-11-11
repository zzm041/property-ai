"""Development-only Task 7 plumbing-skill + fake-provider wiring fixture."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.dev import DEV_PROPERTY_NAME, DEV_TENANT_NAME
from app.model.employee import Employee, EmployeeSkill, Skill
from app.model.identity import User
from app.model.property import Property, Tenant
from app.service.dev_employee_fixture import DEV_STAFF_DISPLAY_NAME
from app.service.dev_seed import require_dev_seed_environment

_BINARY_COLLATION = "utf8mb4_bin"


@dataclass(frozen=True, slots=True)
class DevAiFixtureResult:
    tenant_id: int
    property_id: int
    employee_id: int
    plumbing_skill_id: int


def run_development_ai_fixture(
    settings: Settings,
    *,
    engine_factory=None,
) -> DevAiFixtureResult:
    require_dev_seed_environment(settings)
    owned_engine = engine_factory is None
    factory = engine_factory or create_database_engine
    engine = factory(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        return _ensure_dev_ai(session)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code=DevSeedErrorCode.FAILED,
            message="Development AI fixture failed",
            status_code=500,
        ) from exc
    finally:
        session.close()
        if owned_engine:
            engine.dispose()


def _ensure_dev_ai(session: Session) -> DevAiFixtureResult:
    if session.in_transaction():
        session.rollback()
    with session.begin():
        tenant = session.scalar(
            select(Tenant)
            .where(Tenant.name.collate(_BINARY_COLLATION) == DEV_TENANT_NAME)
            .order_by(Tenant.id)
            .limit(1)
        )
        staff = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == DEV_STAFF_DISPLAY_NAME
            )
        )
        if tenant is None or staff is None:
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development AI fixture is missing employee seed",
                status_code=409,
            )
        property_record = session.scalar(
            select(Property).where(
                Property.tenant_id == tenant.id,
                Property.name.collate(_BINARY_COLLATION) == DEV_PROPERTY_NAME,
            )
        )
        employee = session.scalar(select(Employee).where(Employee.user_id == staff.id))
        if property_record is None or employee is None:
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development AI fixture is missing employee assignment",
                status_code=409,
            )
        skill = session.scalar(
            select(Skill).where(
                Skill.tenant_id == tenant.id,
                Skill.code.collate(_BINARY_COLLATION) == "PLUMBING",
            )
        )
        if skill is None:
            skill = Skill(
                tenant_id=tenant.id,
                code="PLUMBING",
                name="Plumbing",
                status="ACTIVE",
            )
            session.add(skill)
            session.flush()
        link = session.scalar(
            select(EmployeeSkill).where(
                EmployeeSkill.employee_id == employee.id,
                EmployeeSkill.skill_id == skill.id,
            )
        )
        if link is None:
            session.add(
                EmployeeSkill(
                    employee_id=employee.id,
                    tenant_id=employee.tenant_id,
                    skill_id=skill.id,
                )
            )
            session.flush()
        if employee.max_capacity < 8:
            employee.max_capacity = 8
        return DevAiFixtureResult(
            tenant_id=tenant.id,
            property_id=property_record.id,
            employee_id=employee.id,
            plumbing_skill_id=skill.id,
        )
