"""Repository scope and hierarchy Service integration tests."""

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.repository.property import PropertyRepository
from app.service.property import HIERARCHY_NOT_FOUND, PropertyService


def test_repository_queries_require_matching_tenant_scope(
    db_session: Session,
) -> None:
    service = PropertyService(db_session)
    first_tenant = service.create_tenant(name="Tenant 1")
    second_tenant = service.create_tenant(name="Tenant 2")
    first_property = service.create_property(
        tenant_id=first_tenant.id,
        name="Property 1",
        address="Address 1",
    )
    service.create_property(
        tenant_id=second_tenant.id,
        name="Property 2",
        address="Address 2",
    )
    repository = PropertyRepository(db_session)

    assert repository.get_property(
        tenant_id=first_tenant.id,
        property_id=first_property.id,
    ) is not None
    assert (
        repository.get_property(
            tenant_id=second_tenant.id,
            property_id=first_property.id,
        )
        is None
    )
    assert [item.name for item in repository.list_properties(
        tenant_id=first_tenant.id
    )] == ["Property 1"]


def test_scoped_hierarchy_lists_do_not_cross_property(
    db_session: Session,
) -> None:
    service = PropertyService(db_session)
    tenant = service.create_tenant(name="Tenant")
    first_property = service.create_property(
        tenant_id=tenant.id,
        name="Property 1",
        address="Address 1",
    )
    second_property = service.create_property(
        tenant_id=tenant.id,
        name="Property 2",
        address="Address 2",
    )
    first_building = service.create_building(
        tenant_id=tenant.id,
        property_id=first_property.id,
        code="B1",
        name="Building 1",
    )
    service.create_building(
        tenant_id=tenant.id,
        property_id=second_property.id,
        code="B1",
        name="Building 1",
    )
    repository = PropertyRepository(db_session)

    buildings = repository.list_buildings(
        tenant_id=tenant.id,
        property_id=first_property.id,
    )
    assert [item.id for item in buildings] == [first_building.id]


def test_service_rejects_parent_from_another_tenant(
    db_session: Session,
) -> None:
    service = PropertyService(db_session)
    first_tenant = service.create_tenant(name="Tenant 1")
    second_tenant = service.create_tenant(name="Tenant 2")
    property_record = service.create_property(
        tenant_id=first_tenant.id,
        name="Property",
        address="Address",
    )

    with pytest.raises(AppError) as captured:
        service.create_building(
            tenant_id=second_tenant.id,
            property_id=property_record.id,
            code="B1",
            name="Building 1",
        )

    assert captured.value.code == HIERARCHY_NOT_FOUND
