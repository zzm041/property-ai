"""Development-only Building / Unit / House fixture. Not part of IAM Seed."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.dev_seed import DEV_PROPERTY_NAME, DEV_TENANT_NAME
from app.model.property import Building, House, Property, Tenant, Unit
from app.service.dev_seed import require_dev_seed_environment

DEV_BUILDING_CODE = "__DEV_BUILDING__"
DEV_BUILDING_NAME = "Development Only Building"
DEV_UNIT_CODE = "__DEV_UNIT__"
DEV_UNIT_NAME = "Development Only Unit"
DEV_HOUSE_ROOM_NO = "101"

_BINARY_COLLATION = "utf8mb4_bin"


@dataclass(frozen=True, slots=True)
class DevHouseFixtureResult:
    tenant_id: int
    property_id: int
    building_id: int
    unit_id: int
    house_id: int


def run_development_house_fixture(
    settings: Settings,
    *,
    engine_factory=None,
) -> DevHouseFixtureResult:
    require_dev_seed_environment(settings)
    owned_engine = engine_factory is None
    factory = engine_factory or create_database_engine
    engine = factory(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        return _ensure_dev_house(session)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code=DevSeedErrorCode.FAILED,
            message="Development house fixture failed",
            status_code=500,
        ) from exc
    finally:
        session.close()
        if owned_engine:
            engine.dispose()


def _ensure_dev_house(session: Session) -> DevHouseFixtureResult:
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
        building = session.scalar(
            select(Building).where(
                Building.tenant_id == tenant.id,
                Building.property_id == property_record.id,
                Building.code.collate(_BINARY_COLLATION) == DEV_BUILDING_CODE,
            )
        )
        if building is None:
            building = Building(
                tenant_id=tenant.id,
                property_id=property_record.id,
                code=DEV_BUILDING_CODE,
                name=DEV_BUILDING_NAME,
            )
            session.add(building)
            session.flush()
        unit = session.scalar(
            select(Unit).where(
                Unit.tenant_id == tenant.id,
                Unit.property_id == property_record.id,
                Unit.building_id == building.id,
                Unit.code.collate(_BINARY_COLLATION) == DEV_UNIT_CODE,
            )
        )
        if unit is None:
            unit = Unit(
                tenant_id=tenant.id,
                property_id=property_record.id,
                building_id=building.id,
                code=DEV_UNIT_CODE,
                name=DEV_UNIT_NAME,
            )
            session.add(unit)
            session.flush()
        house = session.scalar(
            select(House).where(
                House.tenant_id == tenant.id,
                House.property_id == property_record.id,
                House.unit_id == unit.id,
                House.room_no.collate(_BINARY_COLLATION) == DEV_HOUSE_ROOM_NO,
            )
        )
        if house is None:
            house = House(
                tenant_id=tenant.id,
                property_id=property_record.id,
                unit_id=unit.id,
                room_no=DEV_HOUSE_ROOM_NO,
            )
            session.add(house)
            session.flush()
        return DevHouseFixtureResult(
            tenant_id=tenant.id,
            property_id=property_record.id,
            building_id=building.id,
            unit_id=unit.id,
            house_id=house.id,
        )
