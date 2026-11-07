"""Property Service transaction and validation tests."""

from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.model.property import Building, Property, Tenant
from app.repository.property import PropertyRepository
from app.service.property import (
    HIERARCHY_NOT_FOUND,
    INVALID_INPUT,
    PropertyService,
)


def _service() -> tuple[PropertyService, MagicMock, MagicMock]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    repository = MagicMock(spec=PropertyRepository)
    return PropertyService(session, repository), session, repository


def test_service_owns_transaction_boundary() -> None:
    service, session, repository = _service()
    session.in_transaction.return_value = False
    repository.add_tenant.side_effect = lambda tenant: tenant

    tenant = service.create_tenant(name=" Example Tenant ")

    session.begin.assert_called_once_with()
    repository.add_tenant.assert_called_once()
    assert tenant.name == "Example Tenant"


def test_repository_adds_and_flushes_without_commit_or_rollback() -> None:
    session = MagicMock()
    repository = PropertyRepository(session)
    tenant = Tenant(name="Tenant", status="ACTIVE")

    repository.add_tenant(tenant)

    session.add.assert_called_once_with(tenant)
    session.flush.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_property_timezone_is_validated_and_canonicalized() -> None:
    service, _, repository = _service()
    repository.get_tenant.return_value = Tenant(id=7, name="Tenant", status="ACTIVE")
    repository.add_property.side_effect = lambda property_record: property_record

    property_record = service.create_property(
        tenant_id=7,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )

    assert property_record.tenant_id == 7
    assert property_record.timezone == "Asia/Shanghai"


def test_invalid_property_timezone_is_stable_application_error() -> None:
    service, _, repository = _service()
    repository.get_tenant.return_value = Tenant(id=7, name="Tenant", status="ACTIVE")

    with pytest.raises(AppError) as captured:
        service.create_property(
            tenant_id=7,
            name="Property",
            address="Address",
            timezone="Mars/Olympus_Mons",
        )

    assert captured.value.code == INVALID_INPUT
    repository.add_property.assert_not_called()


def test_child_creation_requires_parent_in_same_scope() -> None:
    service, _, repository = _service()
    repository.get_property.return_value = None

    with pytest.raises(AppError) as captured:
        service.create_building(
            tenant_id=1,
            property_id=20,
            code="B1",
            name="Building 1",
        )

    assert captured.value.code == HIERARCHY_NOT_FOUND
    repository.add_building.assert_not_called()


def test_child_scope_is_copied_from_authoritative_parent() -> None:
    service, _, repository = _service()
    repository.get_property.return_value = Property(
        id=20,
        tenant_id=1,
        name="Property",
        address="Address",
        timezone="Asia/Shanghai",
    )
    repository.add_building.side_effect = lambda building: building

    building = service.create_building(
        tenant_id=1,
        property_id=20,
        code=" B1 ",
        name=" Building 1 ",
    )

    assert isinstance(building, Building)
    assert building.tenant_id == 1
    assert building.property_id == 20
    assert building.code == "B1"
