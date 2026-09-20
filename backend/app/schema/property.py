"""Property archive, batch import/export, and relation-history schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.property import HouseStatus
from app.domain.resident import ResidentRelationStatus


class CreateBuildingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(gt=0)
    property_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class UpdateBuildingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CreateUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(gt=0)
    property_id: int = Field(gt=0)
    building_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class UpdateUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CreateHouseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(gt=0)
    property_id: int = Field(gt=0)
    unit_id: int = Field(gt=0)
    room_no: str = Field(min_length=1, max_length=64)
    floor: int | None = Field(default=None, ge=-128, le=127)
    status: HouseStatus = HouseStatus.ACTIVE


class UpdateHouseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_no: str | None = Field(default=None, min_length=1, max_length=64)
    floor: int | None = Field(default=None, ge=-128, le=127)
    status: HouseStatus | None = None


class HouseImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_code: str = Field(min_length=1, max_length=64)
    building_name: str = Field(min_length=1, max_length=200)
    unit_code: str = Field(min_length=1, max_length=64)
    unit_name: str = Field(min_length=1, max_length=200)
    room_no: str = Field(min_length=1, max_length=64)
    floor: int | None = Field(default=None, ge=-128, le=127)
    status: HouseStatus = HouseStatus.ACTIVE


class ImportHousesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: int = Field(gt=0)
    property_id: int = Field(gt=0)
    rows: list[HouseImportRow] = Field(min_length=1, max_length=500)


class EndResidentRelationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResidentRelationStatus = ResidentRelationStatus.ENDED


class BuildingData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    code: str
    name: str


class UnitData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    building_id: int
    code: str
    name: str


class HouseData(BaseModel):
    id: int
    tenant_id: int
    property_id: int
    unit_id: int
    room_no: str
    floor: int | None
    status: str


class PropertyArchiveData(BaseModel):
    buildings: list[BuildingData]
    units: list[UnitData]
    houses: list[HouseData]


class HouseExportRow(BaseModel):
    building_code: str
    building_name: str
    unit_code: str
    unit_name: str
    room_no: str
    floor: int | None
    status: str


class HouseExportData(BaseModel):
    columns: list[str]
    rows: list[HouseExportRow]


class HouseImportData(BaseModel):
    created_buildings: int
    created_units: int
    created_houses: int
    unchanged_houses: int


class ResidentRelationAdminData(BaseModel):
    id: int
    user_id: int
    user_display_name: str
    house_id: int
    tenant_id: int
    property_id: int
    unit_id: int
    room_no: str
    relation_type: str
    status: str
    valid_from: datetime
    valid_to: datetime | None
    bind_request_id: int | None


class ResidentRelationHistoryData(BaseModel):
    items: list[ResidentRelationAdminData]
    next_cursor: int | None
