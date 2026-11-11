"""Authenticated property catalog for house binding. No resident PII."""

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, IamErrorCode
from app.domain.identity import Principal
from app.model.property import Building, House, Property, Tenant, Unit
from app.repository.property import PropertyRepository


class PropertyCatalogService:
    def __init__(self, session: Session, repository: PropertyRepository | None = None) -> None:
        self._session = session
        self._repository = repository or PropertyRepository(session)

    def list_properties(self, *, actor: Principal, after_id: int = 0, limit: int = 50):
        del actor
        return self._repository.list_active_catalog_properties(
            after_id=max(after_id, 0),
            limit=min(max(limit, 1), 100),
        )

    def list_buildings(self, *, actor: Principal, property_id: int, after_id: int = 0, limit: int = 50):
        del actor
        record = self._require_active_property(property_id)
        return self._repository.list_buildings(
            tenant_id=record.tenant_id,
            property_id=record.id,
            after_id=max(after_id, 0),
            limit=min(max(limit, 1), 100),
        )

    def list_units(self, *, actor: Principal, building_id: int, after_id: int = 0, limit: int = 50):
        del actor
        building = self._session.get(Building, building_id)
        if building is None:
            raise AppError(code=ErrorCode.NOT_FOUND, message="Building was not found", status_code=404)
        self._require_active_property(building.property_id)
        return self._repository.list_units(
            tenant_id=building.tenant_id,
            property_id=building.property_id,
            building_id=building.id,
            after_id=max(after_id, 0),
            limit=min(max(limit, 1), 100),
        )

    def list_houses(self, *, actor: Principal, unit_id: int, after_id: int = 0, limit: int = 50):
        del actor
        unit = self._session.get(Unit, unit_id)
        if unit is None:
            raise AppError(code=ErrorCode.NOT_FOUND, message="Unit was not found", status_code=404)
        self._require_active_property(unit.property_id)
        return self._repository.list_houses(
            tenant_id=unit.tenant_id,
            property_id=unit.property_id,
            unit_id=unit.id,
            after_id=max(after_id, 0),
            limit=min(max(limit, 1), 100),
            active_only=True,
        )

    def _require_active_property(self, property_id: int) -> Property:
        record = self._session.get(Property, property_id)
        if record is None:
            raise AppError(code=ErrorCode.NOT_FOUND, message="Property was not found", status_code=404)
        tenant = self._session.get(Tenant, record.tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        return record
