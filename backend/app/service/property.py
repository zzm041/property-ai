"""Transactional use cases for Tenant and physical Property hierarchy creation."""

from collections.abc import Callable
from typing import NoReturn, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import validate_timezone
from app.model.property import Building, House, Property, Tenant, Unit
from app.repository.property import PropertyRepository

T = TypeVar("T")

INVALID_INPUT = "RES.INVALID_INPUT"
DUPLICATE_RESOURCE = "RES.DUPLICATE_RESOURCE"
HIERARCHY_NOT_FOUND = "RES.HIERARCHY_NOT_FOUND"
HIERARCHY_CONFLICT = "RES.HIERARCHY_CONFLICT"


def _required_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AppError(
            code=INVALID_INPUT,
            message=f"{field} must not be empty",
            status_code=422,
            details={"field": field},
        )
    return normalized


class PropertyService:
    """Own complete transaction boundaries; repositories never commit."""

    def __init__(
        self,
        session: Session,
        repository: PropertyRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or PropertyRepository(session)

    def _execute(self, operation: Callable[[], T], *, composing: bool = False) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            return runner(self._session, operation)
        except IntegrityError as exc:
            error_number = getattr(exc.orig, "args", [None])[0]
            if error_number == 1062:
                raise AppError(
                    code=DUPLICATE_RESOURCE,
                    message="A resource with the same business key already exists",
                    status_code=409,
                ) from exc
            raise AppError(
                code=HIERARCHY_CONFLICT,
                message="Property hierarchy constraint rejected the operation",
                status_code=409,
            ) from exc

    def create_tenant(
        self,
        *,
        name: str,
        status: str = "ACTIVE",
        contact: str | None = None,
    ) -> Tenant:
        def operation() -> Tenant:
            return self._repository.add_tenant(
                Tenant(
                    name=_required_text(name, "name"),
                    status=_required_text(status, "status"),
                    contact=contact.strip() if contact else None,
                )
            )

        return self._execute(operation)

    def create_property(
        self,
        *,
        tenant_id: int,
        name: str,
        address: str,
        timezone: str = "Asia/Shanghai",
    ) -> Property:
        def operation() -> Property:
            tenant = self._repository.get_tenant(tenant_id)
            if tenant is None:
                self._raise_parent_not_found("Tenant")
            try:
                timezone_name = validate_timezone(timezone).key
            except ValueError as exc:
                raise AppError(
                    code=INVALID_INPUT,
                    message="timezone must be a valid IANA timezone",
                    status_code=422,
                    details={"field": "timezone"},
                ) from exc
            return self._repository.add_property(
                Property(
                    tenant_id=tenant.id,
                    name=_required_text(name, "name"),
                    address=_required_text(address, "address"),
                    timezone=timezone_name,
                )
            )

        return self._execute(operation)

    def create_building(
        self,
        *,
        tenant_id: int,
        property_id: int,
        code: str,
        name: str,
        composing: bool = False,
    ) -> Building:
        def operation() -> Building:
            property_record = self._repository.get_property(
                tenant_id=tenant_id,
                property_id=property_id,
            )
            if property_record is None:
                self._raise_parent_not_found("Property")
            return self._repository.add_building(
                Building(
                    tenant_id=property_record.tenant_id,
                    property_id=property_record.id,
                    code=_required_text(code, "code"),
                    name=_required_text(name, "name"),
                )
            )

        return self._execute(operation, composing=composing)

    def create_unit(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int,
        code: str,
        name: str,
        composing: bool = False,
    ) -> Unit:
        def operation() -> Unit:
            building = self._repository.get_building(
                tenant_id=tenant_id,
                property_id=property_id,
                building_id=building_id,
            )
            if building is None:
                self._raise_parent_not_found("Building")
            return self._repository.add_unit(
                Unit(
                    tenant_id=building.tenant_id,
                    property_id=building.property_id,
                    building_id=building.id,
                    code=_required_text(code, "code"),
                    name=_required_text(name, "name"),
                )
            )

        return self._execute(operation, composing=composing)

    def create_house(
        self,
        *,
        tenant_id: int,
        property_id: int,
        unit_id: int,
        room_no: str,
        floor: int | None = None,
        status: str = "ACTIVE",
        composing: bool = False,
    ) -> House:
        def operation() -> House:
            unit = self._repository.get_unit(
                tenant_id=tenant_id,
                property_id=property_id,
                unit_id=unit_id,
            )
            if unit is None:
                self._raise_parent_not_found("Unit")
            return self._repository.add_house(
                House(
                    tenant_id=unit.tenant_id,
                    property_id=unit.property_id,
                    unit_id=unit.id,
                    room_no=_required_text(room_no, "room_no"),
                    floor=floor,
                    status=_required_text(status, "status"),
                )
            )

        return self._execute(operation, composing=composing)

    @staticmethod
    def _raise_parent_not_found(parent: str) -> NoReturn:
        raise AppError(
            code=HIERARCHY_NOT_FOUND,
            message=f"{parent} was not found in the requested scope",
            status_code=404,
        )
