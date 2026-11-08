"""Property-scoped building, unit, house, and import/export routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.core.errors import AppError, ResidentErrorCode
from app.domain.iam import Principal
from app.schema.common import request_id_for, success_envelope
from app.schema.property import (
    BuildingData,
    CreateBuildingRequest,
    CreateHouseRequest,
    CreateUnitRequest,
    HouseData,
    HouseExportData,
    HouseExportRow,
    HouseImportData,
    ImportHousesRequest,
    PropertyArchiveData,
    UnitData,
    UpdateBuildingRequest,
    UpdateHouseRequest,
    UpdateUnitRequest,
)
from app.service.property_admin import PropertyAdminService

router = APIRouter(prefix="/admin", tags=["admin-property"])


def get_property_admin_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> PropertyAdminService:
    return PropertyAdminService(session)


def _building_data(record) -> BuildingData:
    return BuildingData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        code=record.code,
        name=record.name,
    )


def _unit_data(record) -> UnitData:
    return UnitData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        building_id=record.building_id,
        code=record.code,
        name=record.name,
    )


def _house_data(record) -> HouseData:
    return HouseData(
        id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        unit_id=record.unit_id,
        room_no=record.room_no,
        floor=record.floor,
        status=record.status,
    )


@router.get("/property-archive")
def get_property_archive(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int, Query(gt=0)],
) -> dict[str, object]:
    buildings, units, houses = service.list_archive(
        actor=actor, tenant_id=tenant_id, property_id=property_id
    )
    return success_envelope(
        request,
        PropertyArchiveData(
            buildings=[_building_data(item) for item in buildings],
            units=[_unit_data(item) for item in units],
            houses=[_house_data(item) for item in houses],
        ).model_dump(mode="json"),
    )


@router.post("/buildings")
def create_building(
    body: CreateBuildingRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    record = service.create_building(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _building_data(record).model_dump(mode="json"))


@router.patch("/buildings/{building_id}")
def update_building(
    building_id: int,
    body: UpdateBuildingRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    if not body.model_fields_set:
        raise AppError(
            code=ResidentErrorCode.INVALID_INPUT,
            message="At least one building field is required",
            status_code=422,
        )
    record = service.update_building(
        actor=actor,
        building_id=building_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _building_data(record).model_dump(mode="json"))


@router.post("/units")
def create_unit(
    body: CreateUnitRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    record = service.create_unit(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        building_id=body.building_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _unit_data(record).model_dump(mode="json"))


@router.patch("/units/{unit_id}")
def update_unit(
    unit_id: int,
    body: UpdateUnitRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    if not body.model_fields_set:
        raise AppError(
            code=ResidentErrorCode.INVALID_INPUT,
            message="At least one unit field is required",
            status_code=422,
        )
    record = service.update_unit(
        actor=actor,
        unit_id=unit_id,
        code=body.code,
        name=body.name,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _unit_data(record).model_dump(mode="json"))


@router.post("/houses")
def create_house(
    body: CreateHouseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    record = service.create_house(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        unit_id=body.unit_id,
        room_no=body.room_no,
        floor=body.floor,
        status=body.status.value,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _house_data(record).model_dump(mode="json"))


@router.patch("/houses/{house_id}")
def update_house(
    house_id: int,
    body: UpdateHouseRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    if not body.model_fields_set:
        raise AppError(
            code=ResidentErrorCode.INVALID_INPUT,
            message="At least one house field is required",
            status_code=422,
        )
    record = service.update_house(
        actor=actor,
        house_id=house_id,
        room_no=body.room_no,
        floor=body.floor,
        status=body.status.value if body.status is not None else None,
        set_floor="floor" in body.model_fields_set,
        request_id=request_id_for(request),
    )
    return success_envelope(request, _house_data(record).model_dump(mode="json"))


@router.post("/houses/import")
def import_houses(
    body: ImportHousesRequest,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
) -> dict[str, object]:
    result = service.import_houses(
        actor=actor,
        tenant_id=body.tenant_id,
        property_id=body.property_id,
        rows=tuple(body.rows),
        request_id=request_id_for(request),
    )
    return success_envelope(
        request,
        HouseImportData(
            created_buildings=result.created_buildings,
            created_units=result.created_units,
            created_houses=result.created_houses,
            unchanged_houses=result.unchanged_houses,
        ).model_dump(mode="json"),
    )


@router.get("/houses/export")
def export_houses(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyAdminService, Depends(get_property_admin_service)],
    tenant_id: Annotated[int, Query(gt=0)],
    property_id: Annotated[int, Query(gt=0)],
) -> dict[str, object]:
    rows = service.export_houses(
        actor=actor, tenant_id=tenant_id, property_id=property_id
    )
    return success_envelope(
        request,
        HouseExportData(
            columns=[
                "building_code",
                "building_name",
                "unit_code",
                "unit_name",
                "room_no",
                "floor",
                "status",
            ],
            rows=[
                HouseExportRow(
                    building_code=building.code,
                    building_name=building.name,
                    unit_code=unit.code,
                    unit_name=unit.name,
                    room_no=house.room_no,
                    floor=house.floor,
                    status=house.status,
                )
                for building, unit, house in rows
            ],
        ).model_dump(mode="json"),
    )
