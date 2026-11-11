"""Real MySQL 8 schema, uniqueness, and hierarchy constraint tests."""

import pytest
from sqlalchemy import BigInteger, Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.model.property import Building, House, Property, Unit
from app.service.identity.property import DUPLICATE_RESOURCE, PropertyService

BUSINESS_TABLES = {"tenants", "properties", "buildings", "units", "houses"}


def _hierarchy(
    session: Session,
    *,
    tenant_name: str = "Tenant",
    property_name: str = "Property",
) -> tuple[int, int, int, int, int]:
    service = PropertyService(session)
    tenant = service.create_tenant(name=tenant_name)
    property_record = service.create_property(
        tenant_id=tenant.id,
        name=property_name,
        address="Address",
    )
    building = service.create_building(
        tenant_id=tenant.id,
        property_id=property_record.id,
        code="B1",
        name="Building 1",
    )
    unit = service.create_unit(
        tenant_id=tenant.id,
        property_id=property_record.id,
        building_id=building.id,
        code="U1",
        name="Unit 1",
    )
    house = service.create_house(
        tenant_id=tenant.id,
        property_id=property_record.id,
        unit_id=unit.id,
        room_no="101",
        floor=1,
    )
    return tenant.id, property_record.id, building.id, unit.id, house.id


def test_task_2_business_tables_remain_present(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    tables = set(inspector.get_table_names())

    assert BUSINESS_TABLES <= tables
    assert "alembic_version" in tables
    assert "property_units" not in tables
    assert "owner_name" not in {
        column["name"] for column in inspector.get_columns("houses")
    }


def test_primary_and_foreign_keys_are_bigint(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected_foreign_keys = {
        "properties": {"fk_properties_tenant_id_tenants"},
        "buildings": {"fk_buildings_property_tenant_properties"},
        "units": {"fk_units_building_property_tenant_buildings"},
        "houses": {"fk_houses_unit_property_tenant_units"},
    }

    for table in BUSINESS_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert isinstance(columns["id"]["type"], BigInteger)
        assert columns["id"]["autoincrement"] is True
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
        for column_name, column in columns.items():
            if column_name == "id" or column_name.endswith("_id"):
                assert isinstance(column["type"], BigInteger)

    for table, names in expected_foreign_keys.items():
        actual = {item["name"] for item in inspector.get_foreign_keys(table)}
        assert actual == names


def test_composite_foreign_key_columns_are_exact(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected = {
        "buildings": (
            ["property_id", "tenant_id"],
            ["id", "tenant_id"],
        ),
        "units": (
            ["building_id", "property_id", "tenant_id"],
            ["id", "property_id", "tenant_id"],
        ),
        "houses": (
            ["unit_id", "property_id", "tenant_id"],
            ["id", "property_id", "tenant_id"],
        ),
    }

    for table, (local, remote) in expected.items():
        foreign_key = inspect(test_engine).get_foreign_keys(table)[0]
        assert foreign_key["constrained_columns"] == local
        assert foreign_key["referred_columns"] == remote


def test_unique_constraints_and_indexes_are_present(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected_unique = {
        "tenants": {"uq_tenants_name"},
        "properties": {
            "uq_properties_tenant_name",
            "uq_properties_id_tenant",
        },
        "buildings": {
            "uq_buildings_tenant_property_code",
            "uq_buildings_id_property_tenant",
        },
        "units": {
            "uq_units_tenant_property_building_code",
            "uq_units_id_property_tenant",
        },
        "houses": {
            "uq_houses_tenant_property_unit_room",
            "uq_houses_id_property_tenant",
        },
    }
    expected_indexes = {
        "tenants": {"ix_tenants_status_id"},
        "properties": {"ix_properties_tenant_id"},
        "buildings": {
            "ix_buildings_property_tenant",
            "ix_buildings_tenant_property_id",
        },
        "units": {
            "ix_units_building_property_tenant",
            "ix_units_tenant_property_building_id",
        },
        "houses": {
            "ix_houses_unit_property_tenant",
            "ix_houses_scope_unit_id",
            "ix_houses_tenant_property_status_id",
        },
    }

    for table, names in expected_unique.items():
        actual = {
            item["name"] for item in inspector.get_unique_constraints(table)
        }
        assert names <= actual

    for table, names in expected_indexes.items():
        actual = {item["name"] for item in inspector.get_indexes(table)}
        assert names <= actual


def test_duplicate_business_keys_are_rejected(db_session: Session) -> None:
    service = PropertyService(db_session)
    tenant_id, property_id, building_id, unit_id, _ = _hierarchy(db_session)

    operations = (
        lambda: service.create_property(
            tenant_id=tenant_id,
            name="Property",
            address="Duplicate",
        ),
        lambda: service.create_building(
            tenant_id=tenant_id,
            property_id=property_id,
            code="B1",
            name="Duplicate",
        ),
        lambda: service.create_unit(
            tenant_id=tenant_id,
            property_id=property_id,
            building_id=building_id,
            code="U1",
            name="Duplicate",
        ),
        lambda: service.create_house(
            tenant_id=tenant_id,
            property_id=property_id,
            unit_id=unit_id,
            room_no="101",
        ),
    )

    for operation in operations:
        with pytest.raises(AppError) as captured:
            operation()
        assert captured.value.code == DUPLICATE_RESOURCE


def test_same_property_name_is_allowed_in_different_tenants(
    db_session: Session,
) -> None:
    service = PropertyService(db_session)
    first = service.create_tenant(name="Tenant 1")
    second = service.create_tenant(name="Tenant 2")

    first_property = service.create_property(
        tenant_id=first.id,
        name="Shared Name",
        address="Address 1",
    )
    second_property = service.create_property(
        tenant_id=second.id,
        name="Shared Name",
        address="Address 2",
    )

    assert first_property.id != second_property.id


def test_building_cannot_mix_property_and_tenant(db_session: Session) -> None:
    first_tenant, property_id, _, _, _ = _hierarchy(db_session)
    second_tenant = PropertyService(db_session).create_tenant(name="Tenant 2")

    with pytest.raises(IntegrityError):
        with db_session.begin():
            db_session.add(
                Building(
                    tenant_id=second_tenant.id,
                    property_id=property_id,
                    code="INVALID",
                    name="Invalid",
                )
            )
            db_session.flush()

    assert first_tenant != second_tenant.id


def test_unit_cannot_mix_building_and_property(db_session: Session) -> None:
    tenant_id, first_property, building_id, _, _ = _hierarchy(db_session)
    service = PropertyService(db_session)
    second_property = service.create_property(
        tenant_id=tenant_id,
        name="Property 2",
        address="Address 2",
    )

    with pytest.raises(IntegrityError):
        with db_session.begin():
            db_session.add(
                Unit(
                    tenant_id=tenant_id,
                    property_id=second_property.id,
                    building_id=building_id,
                    code="INVALID",
                    name="Invalid",
                )
            )
            db_session.flush()

    assert first_property != second_property.id


def test_house_cannot_mix_unit_and_property(db_session: Session) -> None:
    tenant_id, first_property, _, unit_id, _ = _hierarchy(db_session)
    service = PropertyService(db_session)
    second_property = service.create_property(
        tenant_id=tenant_id,
        name="Property 2",
        address="Address 2",
    )

    with pytest.raises(IntegrityError):
        with db_session.begin():
            db_session.add(
                House(
                    tenant_id=tenant_id,
                    property_id=second_property.id,
                    unit_id=unit_id,
                    room_no="INVALID",
                    status="ACTIVE",
                )
            )
            db_session.flush()

    assert first_property != second_property.id
