"""Sync Session, transaction ownership, and UTC round-trip tests."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory, verify_utc_session
from app.core.errors import AppError
from app.model.property import Property, Tenant
from app.repository.property import PropertyRepository
from app.service.property import DUPLICATE_RESOURCE, PropertyService


def test_sync_session_uses_pymysql_and_utc(
    db_session: Session,
) -> None:
    bind = db_session.get_bind()

    assert bind.dialect.name == "mysql"
    assert bind.dialect.driver == "pymysql"
    connection = db_session.connection()
    verify_utc_session(connection)
    assert connection.execute(text("SELECT @@session.time_zone")).scalar_one() == "+00:00"


def test_service_transaction_commits(
    db_session: Session,
    integration_settings: Settings,
) -> None:
    tenant = PropertyService(db_session).create_tenant(name="Committed Tenant")
    tenant_id = tenant.id
    db_session.close()

    with get_session_factory(integration_settings.test_database_url)() as verification:
        persisted = verification.get(Tenant, tenant_id)
        assert persisted is not None
        assert persisted.name == "Committed Tenant"


def test_repository_flush_without_service_transaction_does_not_commit(
    integration_settings: Settings,
    clean_property_tables: None,
) -> None:
    del clean_property_tables
    factory = get_session_factory(integration_settings.test_database_url)
    session = factory()
    repository = PropertyRepository(session)
    tenant = repository.add_tenant(Tenant(name="Uncommitted Tenant", status="ACTIVE"))
    tenant_id = tenant.id
    session.close()

    with factory() as verification:
        assert verification.get(Tenant, tenant_id) is None


def test_service_rolls_back_failed_duplicate(
    db_session: Session,
) -> None:
    service = PropertyService(db_session)
    tenant = service.create_tenant(name="Tenant")
    service.create_property(
        tenant_id=tenant.id,
        name="Property",
        address="Address 1",
    )

    with pytest.raises(AppError) as captured:
        service.create_property(
            tenant_id=tenant.id,
            name="Property",
            address="Address 2",
        )

    assert captured.value.code == DUPLICATE_RESOURCE
    assert db_session.scalars(select(Property)).all()[0].address == "Address 1"


def test_utc_datetime_round_trip(
    db_session: Session,
) -> None:
    source = datetime(
        2026,
        9,
        15,
        18,
        30,
        45,
        123456,
        tzinfo=timezone(timedelta(hours=8)),
    )
    tenant = Tenant(
        name="UTC Tenant",
        status="ACTIVE",
        created_at=source,
        updated_at=source,
    )
    with db_session.begin():
        db_session.add(tenant)
        db_session.flush()
        tenant_id = tenant.id
    db_session.expunge_all()

    loaded = db_session.get(Tenant, tenant_id)
    assert loaded is not None
    assert loaded.created_at == datetime(
        2026,
        9,
        15,
        10,
        30,
        45,
        123456,
        tzinfo=UTC,
    )
    assert loaded.created_at.tzinfo is UTC
