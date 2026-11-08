"""HOUSE016-B1: SQL filters eligible houses before applying the page limit."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import mysql

from app.model.property import House
from app.repository.property import PropertyRepository
from app.service.property_catalog import PropertyCatalogService


def test_catalog_repository_status_predicate_precedes_limit():
    session=MagicMock()
    repository=PropertyRepository(session)
    repository.list_houses(tenant_id=7,property_id=8,unit_id=9,after_id=11,limit=50,active_only=True)
    sql=str(session.scalars.call_args.args[0].compile(dialect=mysql.dialect(),compile_kwargs={'literal_binds':True}))
    assert "houses.status = 'ACTIVE'" in sql
    assert sql.index("houses.status = 'ACTIVE'") < sql.index('LIMIT 50')
    for expected in ('houses.tenant_id = 7','houses.property_id = 8','houses.unit_id = 9','houses.id > 11','ORDER BY houses.id'):
        assert expected in sql


def test_non_catalog_house_listing_retains_inactive_rows_by_default():
    session=MagicMock()
    PropertyRepository(session).list_houses(tenant_id=7,property_id=8,unit_id=9)
    statement=session.scalars.call_args.args[0]
    assert not any('houses.status' in str(clause) for clause in statement._where_criteria)


def test_catalog_delegates_eligibility_before_pagination_and_keeps_later_ids():
    session=MagicMock(); repository=MagicMock(spec=PropertyRepository)
    session.get.return_value=SimpleNamespace(id=9,tenant_id=7,property_id=8)
    service=PropertyCatalogService(session,repository=repository)
    service._require_active_property=MagicMock()
    # One INACTIVE physical ID is skipped by the repository, not after limit.
    first=tuple(House(id=i,status='ACTIVE') for i in range(1,52) if i!=10)
    later=(House(id=52,status='ACTIVE'),House(id=53,status='ACTIVE'))
    repository.list_houses.side_effect=[first,later]
    assert len(service.list_houses(actor=MagicMock(),unit_id=9,limit=50))==50
    repository.list_houses.assert_called_with(tenant_id=7,property_id=8,unit_id=9,after_id=0,limit=50,active_only=True)
    result=service.list_houses(actor=MagicMock(),unit_id=9,after_id=51,limit=50)
    assert [item.id for item in result]==[52,53]
    repository.list_houses.assert_called_with(tenant_id=7,property_id=8,unit_id=9,after_id=51,limit=50,active_only=True)
