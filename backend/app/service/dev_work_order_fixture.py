"""Development-only WorkOrder SLA + resident-house bootstrap. Not IAM Seed."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.core.time import utc_now
from app.domain.dev import DEV_PROPERTY_NAME, DEV_TENANT_NAME
from app.domain.identity import ResidentRelationStatus, ResidentRelationType
from app.domain.work_order import (
    DEFAULT_SLA_COMPLETE_MINUTES,
    DEFAULT_SLA_RESPOND_MINUTES,
    WorkOrderCategory,
    WorkOrderPriority,
)
from app.model.identity import User
from app.model.property import House, Property, Tenant
from app.model.resident import ResidentHouseRelation
from app.model.work_order import SlaPolicy
from app.service.dev_house_fixture import DEV_HOUSE_ROOM_NO
from app.service.dev_seed import require_dev_seed_environment

DEV_RESIDENT_DISPLAY_NAME = "__DEV_RESIDENT__"
_BINARY_COLLATION = "utf8mb4_bin"


@dataclass(frozen=True, slots=True)
class DevWorkOrderFixtureResult:
    tenant_id: int
    property_id: int
    house_id: int
    resident_user_id: int
    relation_id: int
    sla_policy_count: int


def run_development_work_order_fixture(
    settings: Settings,
    *,
    engine_factory=None,
) -> DevWorkOrderFixtureResult:
    require_dev_seed_environment(settings)
    owned_engine = engine_factory is None
    factory = engine_factory or create_database_engine
    engine = factory(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        return _ensure_dev_work_order(session)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code=DevSeedErrorCode.FAILED,
            message="Development work-order fixture failed",
            status_code=500,
        ) from exc
    finally:
        session.close()
        if owned_engine:
            engine.dispose()


def _ensure_dev_work_order(session: Session) -> DevWorkOrderFixtureResult:
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
        house = session.scalar(
            select(House).where(
                House.tenant_id == tenant.id,
                House.property_id == property_record.id,
                House.room_no.collate(_BINARY_COLLATION) == DEV_HOUSE_ROOM_NO,
            )
        )
        if house is None:
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development house is missing; run house fixture first",
                status_code=409,
            )
        resident = session.scalar(
            select(User)
            .where(
                User.display_name.collate(_BINARY_COLLATION)
                == DEV_RESIDENT_DISPLAY_NAME
            )
            .order_by(User.id)
            .limit(1)
        )
        if resident is None or resident.status != "ACTIVE":
            raise AppError(
                code=DevSeedErrorCode.DATA_CONFLICT,
                message="Development resident is missing; run IAM seed first",
                status_code=409,
            )
        now = utc_now()
        relation = session.scalar(
            select(ResidentHouseRelation).where(
                ResidentHouseRelation.user_id == resident.id,
                ResidentHouseRelation.house_id == house.id,
                ResidentHouseRelation.status == ResidentRelationStatus.ACTIVE.value,
            )
        )
        if relation is None:
            relation = ResidentHouseRelation(
                user_id=resident.id,
                tenant_id=tenant.id,
                property_id=property_record.id,
                house_id=house.id,
                relation_type=ResidentRelationType.OWNER.value,
                status=ResidentRelationStatus.ACTIVE.value,
                valid_from=now,
                valid_to=None,
            )
            session.add(relation)
            session.flush()
        policy_count = 0
        for category in WorkOrderCategory:
            for priority in WorkOrderPriority:
                existing = session.scalar(
                    select(SlaPolicy).where(
                        SlaPolicy.tenant_id == tenant.id,
                        SlaPolicy.property_id.is_(None),
                        SlaPolicy.category == category.value,
                        SlaPolicy.priority == priority.value,
                    )
                )
                if existing is None:
                    session.add(
                        SlaPolicy(
                            tenant_id=tenant.id,
                            property_id=None,
                            category=category.value,
                            priority=priority.value,
                            respond_minutes=DEFAULT_SLA_RESPOND_MINUTES[priority],
                            complete_minutes=DEFAULT_SLA_COMPLETE_MINUTES[priority],
                            status="ACTIVE",
                        )
                    )
                    session.flush()
                    policy_count += 1
                else:
                    policy_count += 1
        return DevWorkOrderFixtureResult(
            tenant_id=tenant.id,
            property_id=property_record.id,
            house_id=house.id,
            resident_user_id=resident.id,
            relation_id=relation.id,
            sla_policy_count=policy_count,
        )
