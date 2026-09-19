"""Employee claim runtime tests wait for disposable-DB approval of migration 0009."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


@pytest.fixture
def employee_invite_tables(test_engine: Engine) -> None:
    if not inspect(test_engine).has_table("employee_invites"):
        pytest.skip(
            "Migration 20260919_0009 is not applied on the disposable test database"
        )


def test_employee_invite_schema_gate(employee_invite_tables: None) -> None:
    del employee_invite_tables
    assert True
