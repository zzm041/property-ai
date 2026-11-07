"""Authenticated physical-house catalog. Responses contain no resident PII."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies.auth import get_current_principal
from app.api.dependencies.db import get_db_session
from app.domain.iam import Principal
from app.schema.common import success_envelope
from app.service.property_catalog import PropertyCatalogService
from sqlalchemy.orm import Session

router = APIRouter(tags=["property-catalog"])


def get_catalog_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> PropertyCatalogService:
    return PropertyCatalogService(session)


@router.get("/properties")
def list_properties(
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyCatalogService, Depends(get_catalog_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items = [
        {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "name": item.name,
            "address": item.address,
        }
        for item in service.list_properties(actor=actor, after_id=after_id, limit=limit)
    ]
    return success_envelope(request, {"items": items})


@router.get("/properties/{property_id}/buildings")
def list_buildings(
    property_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyCatalogService, Depends(get_catalog_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items = [
        {"id": item.id, "property_id": item.property_id, "code": item.code, "name": item.name}
        for item in service.list_buildings(
            actor=actor, property_id=property_id, after_id=after_id, limit=limit
        )
    ]
    return success_envelope(request, {"items": items})


@router.get("/buildings/{building_id}/units")
def list_units(
    building_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyCatalogService, Depends(get_catalog_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items = [
        {
            "id": item.id,
            "building_id": item.building_id,
            "code": item.code,
            "name": item.name,
        }
        for item in service.list_units(
            actor=actor, building_id=building_id, after_id=after_id, limit=limit
        )
    ]
    return success_envelope(request, {"items": items})


@router.get("/units/{unit_id}/houses")
def list_houses(
    unit_id: int,
    request: Request,
    actor: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PropertyCatalogService, Depends(get_catalog_service)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    items = [
        {
            "id": item.id,
            "unit_id": item.unit_id,
            "room_no": item.room_no,
            "floor": item.floor,
            "status": item.status,
        }
        for item in service.list_houses(
            actor=actor, unit_id=unit_id, after_id=after_id, limit=limit
        )
    ]
    return success_envelope(request, {"items": items})
