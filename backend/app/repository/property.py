"""Scoped persistence operations for the physical Property hierarchy."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.property import Building, House, Property, Tenant, Unit


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

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        return self._session.scalar(
            select(Tenant).where(Tenant.id == tenant_id)
        )

    def get_property(self, *, tenant_id: int, property_id: int) -> Property | None:
        return self._session.scalar(
            select(Property).where(
                Property.id == property_id,
                Property.tenant_id == tenant_id,
            )
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

    def list_houses(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[House]:
        return self._session.scalars(
            select(House)
            .where(
                House.tenant_id == tenant_id,
                House.property_id == property_id,
                House.unit_id == unit_id,
                House.id > after_id,
            )
            .order_by(House.id)
            .limit(limit)
        ).all()
