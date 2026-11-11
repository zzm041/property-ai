"""Scoped property-archive maintenance and atomic house batch import."""

from dataclasses import dataclass
from typing import Callable, TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, IamErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.property import HouseStatus, PROPERTY_MANAGE, PROPERTY_READ
from app.model.property import Building, House, Property, Unit
from app.repository.identity import IdentityRepository
from app.repository.property import PropertyRepository
from app.schema.property import HouseImportRow
from app.service.audit import AuditService
from app.service.identity.auth import AuthService
from app.service.identity.property import PropertyService
from app.service.identity.rbac import RbacService

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class HouseImportResult:
    created_buildings: int
    created_units: int
    created_houses: int
    unchanged_houses: int


class PropertyAdminService:
    """Use existing hierarchy rules while adding scoped admin transactions."""

    def __init__(
        self,
        session: Session,
        repository: PropertyRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or PropertyRepository(session)
        self._property_service = PropertyService(session, self._repository)
        self._audit_service = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session, IdentityRepository(session)
        ).load_principal

    def list_archive(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
    ) -> tuple[tuple[Building, ...], tuple[Unit, ...], tuple[House, ...]]:
        current = self._reload_actor(actor)
        record = self._require_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=PROPERTY_READ,
        )
        return (
            tuple(
                self._repository.list_all_buildings(
                    tenant_id=record.tenant_id, property_id=record.id
                )
            ),
            tuple(
                self._repository.list_all_units(
                    tenant_id=record.tenant_id, property_id=record.id
                )
            ),
            tuple(
                self._repository.list_all_houses(
                    tenant_id=record.tenant_id, property_id=record.id
                )
            ),
        )

    def create_building(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        request_id: str,
    ) -> Building:
        return self._execute(
            lambda: self._create_building(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                code=code,
                name=name,
                request_id=request_id,
                batch_import=False,
            )
        )

    def create_unit(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        building_id: int,
        code: str,
        name: str,
        request_id: str,
    ) -> Unit:
        return self._execute(
            lambda: self._create_unit(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                building_id=building_id,
                code=code,
                name=name,
                request_id=request_id,
                batch_import=False,
            )
        )

    def update_building(
        self,
        *,
        actor: Principal,
        building_id: int,
        request_id: str,
        code: str | None = None,
        name: str | None = None,
    ) -> Building:
        return self._execute(
            lambda: self._update_building(
                actor=actor,
                building_id=building_id,
                request_id=request_id,
                code=code,
                name=name,
            )
        )

    def update_unit(
        self,
        *,
        actor: Principal,
        unit_id: int,
        request_id: str,
        code: str | None = None,
        name: str | None = None,
    ) -> Unit:
        return self._execute(
            lambda: self._update_unit(
                actor=actor,
                unit_id=unit_id,
                request_id=request_id,
                code=code,
                name=name,
            )
        )

    def create_house(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        room_no: str,
        floor: int | None,
        status: str,
        request_id: str,
    ) -> House:
        return self._execute(
            lambda: self._create_house(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                unit_id=unit_id,
                room_no=room_no,
                floor=floor,
                status=status,
                request_id=request_id,
                batch_import=False,
            )
        )

    def update_house(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        room_no: str | None = None,
        floor: int | None = None,
        status: str | None = None,
        set_floor: bool = False,
    ) -> House:
        return self._execute(
            lambda: self._update_house(
                actor=actor,
                house_id=house_id,
                request_id=request_id,
                room_no=room_no,
                floor=floor,
                status=status,
                set_floor=set_floor,
            )
        )

    def import_houses(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        rows: tuple[HouseImportRow, ...],
        request_id: str,
    ) -> HouseImportResult:
        return self._execute(
            lambda: self._import_houses(
                actor=actor,
                tenant_id=tenant_id,
                property_id=property_id,
                rows=rows,
                request_id=request_id,
            )
        )

    def export_houses(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
    ) -> tuple[tuple[Building, Unit, House], ...]:
        buildings, units, houses = self.list_archive(
            actor=actor, tenant_id=tenant_id, property_id=property_id
        )
        building_by_id = {record.id: record for record in buildings}
        unit_by_id = {record.id: record for record in units}
        rows: list[tuple[Building, Unit, House]] = []
        for house in houses:
            unit = unit_by_id[house.unit_id]
            rows.append((building_by_id[unit.building_id], unit, house))
        return tuple(rows)

    def _create_building(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        request_id: str,
        batch_import: bool,
    ) -> Building:
        current, record = self._require_manage(actor, tenant_id, property_id)
        building = self._property_service.create_building(
            tenant_id=record.tenant_id,
            property_id=record.id,
            code=code,
            name=name,
            composing=True,
        )
        self._append_audit(
            action=AuditAction.PROPERTY_BUILDING_CREATED,
            resource_type=AuditResourceType.BUILDING,
            actor=current,
            resource_id=building.id,
            request_id=request_id,
            property_record=record,
            details={
                "code": building.code,
                "name": building.name,
                "batch_import": batch_import,
            },
        )
        return building

    def _create_unit(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        building_id: int,
        code: str,
        name: str,
        request_id: str,
        batch_import: bool,
    ) -> Unit:
        current, record = self._require_manage(actor, tenant_id, property_id)
        unit = self._property_service.create_unit(
            tenant_id=record.tenant_id,
            property_id=record.id,
            building_id=building_id,
            code=code,
            name=name,
            composing=True,
        )
        self._append_audit(
            action=AuditAction.PROPERTY_UNIT_CREATED,
            resource_type=AuditResourceType.UNIT,
            actor=current,
            resource_id=unit.id,
            request_id=request_id,
            property_record=record,
            details={
                "building_id": unit.building_id,
                "code": unit.code,
                "name": unit.name,
                "batch_import": batch_import,
            },
        )
        return unit

    def _create_house(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        room_no: str,
        floor: int | None,
        status: str,
        request_id: str,
        batch_import: bool,
    ) -> House:
        current, record = self._require_manage(actor, tenant_id, property_id)
        resolved_status = self._house_status(status)
        house = self._property_service.create_house(
            tenant_id=record.tenant_id,
            property_id=record.id,
            unit_id=unit_id,
            room_no=room_no,
            floor=floor,
            status=resolved_status,
            composing=True,
        )
        self._append_house_audit(
            action=AuditAction.PROPERTY_HOUSE_CREATED,
            actor=current,
            house=house,
            request_id=request_id,
            property_record=record,
            batch_import=batch_import,
        )
        return house

    def _update_building(
        self,
        *,
        actor: Principal,
        building_id: int,
        request_id: str,
        code: str | None,
        name: str | None,
    ) -> Building:
        self._positive_id(building_id, "building_id")
        if code is None and name is None:
            self._invalid("fields")
        current = self._reload_actor(actor)
        existing = self._session.get(Building, building_id)
        if existing is None:
            self._hierarchy_not_found("Building")
        record = self._require_property(
            current,
            tenant_id=existing.tenant_id,
            property_id=existing.property_id,
            permission=PROPERTY_MANAGE,
        )
        building = self._repository.get_building_for_update(
            tenant_id=record.tenant_id,
            property_id=record.id,
            building_id=building_id,
        )
        if building is None:
            self._hierarchy_not_found("Building")
        if code is not None:
            building.code = self._normalized_text(code, "code", 64)
        if name is not None:
            building.name = self._normalized_text(name, "name", 200)
        self._session.flush()
        self._append_audit(
            action=AuditAction.PROPERTY_BUILDING_UPDATED,
            resource_type=AuditResourceType.BUILDING,
            actor=current,
            resource_id=building.id,
            request_id=request_id,
            property_record=record,
            details={"code": building.code, "name": building.name},
        )
        return building

    def _update_unit(
        self,
        *,
        actor: Principal,
        unit_id: int,
        request_id: str,
        code: str | None,
        name: str | None,
    ) -> Unit:
        self._positive_id(unit_id, "unit_id")
        if code is None and name is None:
            self._invalid("fields")
        current = self._reload_actor(actor)
        existing = self._session.get(Unit, unit_id)
        if existing is None:
            self._hierarchy_not_found("Unit")
        record = self._require_property(
            current,
            tenant_id=existing.tenant_id,
            property_id=existing.property_id,
            permission=PROPERTY_MANAGE,
        )
        unit = self._repository.get_unit_for_update(
            tenant_id=record.tenant_id,
            property_id=record.id,
            unit_id=unit_id,
        )
        if unit is None:
            self._hierarchy_not_found("Unit")
        if code is not None:
            unit.code = self._normalized_text(code, "code", 64)
        if name is not None:
            unit.name = self._normalized_text(name, "name", 200)
        self._session.flush()
        self._append_audit(
            action=AuditAction.PROPERTY_UNIT_UPDATED,
            resource_type=AuditResourceType.UNIT,
            actor=current,
            resource_id=unit.id,
            request_id=request_id,
            property_record=record,
            details={
                "building_id": unit.building_id,
                "code": unit.code,
                "name": unit.name,
            },
        )
        return unit

    def _update_house(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        room_no: str | None,
        floor: int | None,
        status: str | None,
        set_floor: bool,
    ) -> House:
        self._positive_id(house_id, "house_id")
        current = self._reload_actor(actor)
        existing = self._session.get(House, house_id)
        if existing is None:
            raise AppError(
                code="RES.HIERARCHY_NOT_FOUND",
                message="House was not found in the requested scope",
                status_code=404,
            )
        record = self._require_property(
            current,
            tenant_id=existing.tenant_id,
            property_id=existing.property_id,
            permission=PROPERTY_MANAGE,
        )
        house = self._repository.get_house_for_update(
            tenant_id=record.tenant_id,
            property_id=record.id,
            house_id=house_id,
        )
        if house is None:
            raise AppError(
                code="RES.HIERARCHY_NOT_FOUND",
                message="House was not found in the requested scope",
                status_code=404,
            )
        if room_no is not None:
            normalized = room_no.strip()
            if not normalized:
                self._invalid("room_no")
            house.room_no = normalized
        if set_floor:
            house.floor = floor
        if status is not None:
            house.status = self._house_status(status)
        self._session.flush()
        self._append_house_audit(
            action=AuditAction.PROPERTY_HOUSE_UPDATED,
            actor=current,
            house=house,
            request_id=request_id,
            property_record=record,
            batch_import=None,
        )
        return house

    def _import_houses(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int,
        rows: tuple[HouseImportRow, ...],
        request_id: str,
    ) -> HouseImportResult:
        if not rows or len(rows) > 500:
            self._invalid("rows")
        current, record = self._require_manage(actor, tenant_id, property_id)
        buildings: dict[str, Building] = {}
        units: dict[tuple[int, str], Unit] = {}
        created_buildings = created_units = created_houses = unchanged_houses = 0
        seen_houses: set[tuple[str, str, str]] = set()

        for index, row in enumerate(rows, start=1):
            building_code = row.building_code.strip()
            building_name = row.building_name.strip()
            unit_code = row.unit_code.strip()
            unit_name = row.unit_name.strip()
            room_no = row.room_no.strip()
            if not all((building_code, building_name, unit_code, unit_name, room_no)):
                self._invalid("rows", row=index)
            row_key = (building_code, unit_code, room_no)
            if row_key in seen_houses:
                raise AppError(
                    code="RES.DUPLICATE_RESOURCE",
                    message="The import contains a duplicate house row",
                    status_code=409,
                    details={"row": index},
                )
            seen_houses.add(row_key)

            building = buildings.get(building_code)
            if building is None:
                building = self._repository.get_building_by_code(
                    tenant_id=record.tenant_id,
                    property_id=record.id,
                    code=building_code,
                )
                if building is None:
                    building = self._property_service.create_building(
                        tenant_id=record.tenant_id,
                        property_id=record.id,
                        code=building_code,
                        name=building_name,
                        composing=True,
                    )
                    created_buildings += 1
                    self._append_audit(
                        action=AuditAction.PROPERTY_BUILDING_CREATED,
                        resource_type=AuditResourceType.BUILDING,
                        actor=current,
                        resource_id=building.id,
                        request_id=request_id,
                        property_record=record,
                        details={
                            "code": building.code,
                            "name": building.name,
                            "batch_import": True,
                        },
                    )
                buildings[building_code] = building
            if building.name != building_name:
                self._import_conflict(index, "building_name")

            unit_key = (building.id, unit_code)
            unit = units.get(unit_key)
            if unit is None:
                unit = self._repository.get_unit_by_code(
                    tenant_id=record.tenant_id,
                    property_id=record.id,
                    building_id=building.id,
                    code=unit_code,
                )
                if unit is None:
                    unit = self._property_service.create_unit(
                        tenant_id=record.tenant_id,
                        property_id=record.id,
                        building_id=building.id,
                        code=unit_code,
                        name=unit_name,
                        composing=True,
                    )
                    created_units += 1
                    self._append_audit(
                        action=AuditAction.PROPERTY_UNIT_CREATED,
                        resource_type=AuditResourceType.UNIT,
                        actor=current,
                        resource_id=unit.id,
                        request_id=request_id,
                        property_record=record,
                        details={
                            "building_id": building.id,
                            "code": unit.code,
                            "name": unit.name,
                            "batch_import": True,
                        },
                    )
                units[unit_key] = unit
            if unit.name != unit_name:
                self._import_conflict(index, "unit_name")

            status = row.status.value
            house = self._repository.get_house_by_room(
                tenant_id=record.tenant_id,
                property_id=record.id,
                unit_id=unit.id,
                room_no=room_no,
            )
            if house is not None:
                if house.floor != row.floor or house.status != status:
                    self._import_conflict(index, "house")
                unchanged_houses += 1
                continue
            house = self._property_service.create_house(
                tenant_id=record.tenant_id,
                property_id=record.id,
                unit_id=unit.id,
                room_no=room_no,
                floor=row.floor,
                status=status,
                composing=True,
            )
            created_houses += 1
            self._append_house_audit(
                action=AuditAction.PROPERTY_HOUSE_CREATED,
                actor=current,
                house=house,
                request_id=request_id,
                property_record=record,
                batch_import=True,
            )

        return HouseImportResult(
            created_buildings=created_buildings,
            created_units=created_units,
            created_houses=created_houses,
            unchanged_houses=unchanged_houses,
        )

    def _require_manage(
        self, actor: Principal, tenant_id: int, property_id: int
    ) -> tuple[Principal, Property]:
        current = self._reload_actor(actor)
        return current, self._require_property(
            current,
            tenant_id=tenant_id,
            property_id=property_id,
            permission=PROPERTY_MANAGE,
        )

    def _require_property(
        self,
        actor: Principal,
        *,
        tenant_id: int,
        property_id: int,
        permission: str,
    ) -> Property:
        self._positive_id(tenant_id, "tenant_id")
        self._positive_id(property_id, "property_id")
        record = self._repository.get_property(
            tenant_id=tenant_id, property_id=property_id
        )
        if record is None:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            actor,
            permission,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=record.tenant_id,
                property_id=record.id,
            ),
        )
        return record

    def _reload_actor(self, actor: Principal) -> Principal:
        if not isinstance(actor, Principal) or actor.user_id <= 0:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        try:
            current = self._principal_loader(actor.user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if current.user_id != actor.user_id or current.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return current

    def _append_house_audit(
        self,
        *,
        action: AuditAction,
        actor: Principal,
        house: House,
        request_id: str,
        property_record: Property,
        batch_import: bool | None,
    ) -> None:
        details: dict[str, object] = {
            "unit_id": house.unit_id,
            "room_no": house.room_no,
            "floor": house.floor,
            "status": house.status,
        }
        if batch_import is not None:
            details["batch_import"] = batch_import
        self._append_audit(
            action=action,
            resource_type=AuditResourceType.HOUSE,
            actor=actor,
            resource_id=house.id,
            request_id=request_id,
            property_record=property_record,
            details=details,
        )

    def _append_audit(
        self,
        *,
        action: AuditAction,
        resource_type: AuditResourceType,
        actor: Principal,
        resource_id: int,
        request_id: str,
        property_record: Property,
        details: dict[str, object],
    ) -> None:
        self._audit_service.append(
            action=action,
            resource_type=resource_type,
            actor_user_id=actor.user_id,
            resource_id=resource_id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            details=details,
        )

    def _execute(self, operation: Callable[[], T]) -> T:
        from app.core.database import run_owned_transaction

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code="RES.DUPLICATE_RESOURCE",
                message="A resource with the same business key already exists",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Property archive operation could not be completed",
                status_code=500,
            ) from exc

    @staticmethod
    def _hierarchy_not_found(label: str) -> None:
        raise AppError(
            code="RES.HIERARCHY_NOT_FOUND",
            message=f"{label} was not found in the requested scope",
            status_code=404,
        )

    @staticmethod
    def _normalized_text(value: str, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            PropertyAdminService._invalid(field)
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            PropertyAdminService._invalid(field)
        return normalized

    @staticmethod
    def _house_status(value: str) -> str:
        try:
            return HouseStatus(value).value
        except ValueError as exc:
            raise AppError(
                code="RES.INVALID_INPUT",
                message="status is invalid",
                status_code=422,
                details={"field": "status"},
            ) from exc

    @staticmethod
    def _positive_id(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            PropertyAdminService._invalid(field)

    @staticmethod
    def _invalid(field: str, *, row: int | None = None) -> None:
        details: dict[str, object] = {"field": field}
        if row is not None:
            details["row"] = row
        raise AppError(
            code="RES.INVALID_INPUT",
            message=f"{field} is invalid",
            status_code=422,
            details=details,
        )

    @staticmethod
    def _import_conflict(row: int, field: str) -> None:
        raise AppError(
            code="RES.HIERARCHY_CONFLICT",
            message="The import conflicts with an existing archive record",
            status_code=409,
            details={"row": row, "field": field},
        )
