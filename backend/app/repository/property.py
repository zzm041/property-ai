"""Scoped persistence operations for the physical Property hierarchy."""

from collections.abc import Sequence

from sqlalchemy import and_, select, tuple_
from sqlalchemy.orm import Session

from app.model.property import Building, House, Property, Tenant, Unit
from app.model.work_order import IdempotencyRecord


class PropertyRepository:
    """Query/add/flush only. Transaction commit and rollback belong to Service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_tenant(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        self._session.flush()
        return tenant

    def add_property(self, property_record: Property) -> Property:
        self._session.add(property_record)
        self._session.flush()
        return property_record

    def add_building(self, building: Building) -> Building:
        self._session.add(building)
        self._session.flush()
        return building

    def add_unit(self, unit: Unit) -> Unit:
        self._session.add(unit)
        self._session.flush()
        return unit

    def add_house(self, house: House) -> House:
        self._session.add(house)
        self._session.flush()
        return house

    def get_idempotency(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        return self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    def add_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        return self._session.scalar(
            select(Tenant).where(Tenant.id == tenant_id)
        )

    def house_address_labels(self, houses: Sequence[House]) -> dict[int, dict[str, str]]:
        """Batch presentation for already-authorized houses; no catalog expansion."""
        if not houses:
            return {}
        rows = self._session.execute(
            select(
                House.id.label("house_id"),
                Property.name.label("property_name"),
                Property.address.label("property_address"),
                Building.name.label("building_name"),
                Unit.name.label("unit_name"),
            )
            .select_from(House)
            .join(Unit, and_(Unit.id == House.unit_id, Unit.tenant_id == House.tenant_id, Unit.property_id == House.property_id))
            .join(Building, and_(Building.id == Unit.building_id, Building.tenant_id == House.tenant_id, Building.property_id == House.property_id))
            .join(Property, and_(Property.id == House.property_id, Property.tenant_id == House.tenant_id))
            .where(tuple_(House.id, House.tenant_id, House.property_id).in_(
                [(house.id, house.tenant_id, house.property_id) for house in houses]
            ))
        ).mappings().all()
        return {row["house_id"]: {key: row[key] for key in ("property_name", "property_address", "building_name", "unit_name")} for row in rows}

    def get_tenant_for_update(self, tenant_id: int) -> Tenant | None:
        return self._session.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_property(self, *, tenant_id: int, property_id: int) -> Property | None:
        return self._session.scalar(
            select(Property).where(
                Property.id == property_id,
                Property.tenant_id == tenant_id,
            )
        )

    def get_property_by_id(self, property_id: int) -> Property | None:
        return self._session.get(Property, property_id)

    def get_property_by_id_for_update(self, property_id: int) -> Property | None:
        return self._session.scalar(
            select(Property)
            .where(Property.id == property_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_building(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int,
    ) -> Building | None:
        return self._session.scalar(
            select(Building).where(
                Building.id == building_id,
                Building.tenant_id == tenant_id,
                Building.property_id == property_id,
            )
        )

    def get_building_by_code(
        self,
        *,
        tenant_id: int,
        property_id: int,
        code: str,
    ) -> Building | None:
        return self._session.scalar(
            select(Building).where(
                Building.tenant_id == tenant_id,
                Building.property_id == property_id,
                Building.code == code,
            )
        )

    def get_building_for_update(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int,
    ) -> Building | None:
        return self._session.scalar(
            select(Building)
            .where(
                Building.id == building_id,
                Building.tenant_id == tenant_id,
                Building.property_id == property_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_unit(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
    ) -> Unit | None:
        return self._session.scalar(
            select(Unit).where(
                Unit.id == unit_id,
                Unit.tenant_id == tenant_id,
                Unit.property_id == property_id,
            )
        )

    def get_unit_by_code(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int,
        code: str,
    ) -> Unit | None:
        return self._session.scalar(
            select(Unit).where(
                Unit.tenant_id == tenant_id,
                Unit.property_id == property_id,
                Unit.building_id == building_id,
                Unit.code == code,
            )
        )

    def get_unit_for_update(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
    ) -> Unit | None:
        return self._session.scalar(
            select(Unit)
            .where(
                Unit.id == unit_id,
                Unit.tenant_id == tenant_id,
                Unit.property_id == property_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_house(
        self,
        *,
        tenant_id: int,
        property_id: int,
        house_id: int,
    ) -> House | None:
        return self._session.scalar(
            select(House).where(
                House.id == house_id,
                House.tenant_id == tenant_id,
                House.property_id == property_id,
            )
        )

    def get_house_for_update(
        self,
        *,
        tenant_id: int,
        property_id: int,
        house_id: int,
    ) -> House | None:
        return self._session.scalar(
            select(House)
            .where(
                House.id == house_id,
                House.tenant_id == tenant_id,
                House.property_id == property_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_house_by_room(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        room_no: str,
    ) -> House | None:
        return self._session.scalar(
            select(House).where(
                House.tenant_id == tenant_id,
                House.property_id == property_id,
                House.unit_id == unit_id,
                House.room_no == room_no,
            )
        )

    def list_properties(
        self,
        *,
        tenant_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[Property]:
        return self._session.scalars(
            select(Property)
            .where(
                Property.tenant_id == tenant_id,
                Property.id > after_id,
            )
            .order_by(Property.id)
            .limit(limit)
        ).all()

    def list_active_catalog_properties(
        self,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[Property]:
        return self._session.scalars(
            select(Property)
            .join(Tenant, Tenant.id == Property.tenant_id)
            .where(
                Tenant.status == "ACTIVE",
                Property.id > after_id,
            )
            .order_by(Property.id)
            .limit(limit)
        ).all()

    def list_buildings(
        self,
        *,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[Building]:
        return self._session.scalars(
            select(Building)
            .where(
                Building.tenant_id == tenant_id,
                Building.property_id == property_id,
                Building.id > after_id,
            )
            .order_by(Building.id)
            .limit(limit)
        ).all()

    def list_all_properties(self, *, tenant_id: int) -> Sequence[Property]:
        return self._session.scalars(
            select(Property)
            .where(Property.tenant_id == tenant_id)
            .order_by(Property.id)
        ).all()

    def list_all_buildings(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> Sequence[Building]:
        return self._session.scalars(
            select(Building)
            .where(
                Building.tenant_id == tenant_id,
                Building.property_id == property_id,
            )
            .order_by(Building.id)
        ).all()

    def list_units(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[Unit]:
        return self._session.scalars(
            select(Unit)
            .where(
                Unit.tenant_id == tenant_id,
                Unit.property_id == property_id,
                Unit.building_id == building_id,
                Unit.id > after_id,
            )
            .order_by(Unit.id)
            .limit(limit)
        ).all()

    def list_all_units(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> Sequence[Unit]:
        return self._session.scalars(
            select(Unit)
            .where(
                Unit.tenant_id == tenant_id,
                Unit.property_id == property_id,
            )
            .order_by(Unit.id)
        ).all()

    def list_houses(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        after_id: int = 0,
        limit: int = 100,
        active_only: bool = False,
    ) -> Sequence[House]:
        statement = select(House).where(
            House.tenant_id == tenant_id,
            House.property_id == property_id,
            House.unit_id == unit_id,
            House.id > after_id,
        )
        if active_only:
            statement = statement.where(House.status == "ACTIVE")
        return self._session.scalars(statement.order_by(House.id).limit(limit)).all()

    def list_all_houses(
        self,
        *,
        tenant_id: int,
        property_id: int,
    ) -> Sequence[House]:
        return self._session.scalars(
            select(House)
            .where(
                House.tenant_id == tenant_id,
                House.property_id == property_id,
            )
            .order_by(House.id)
        ).all()
