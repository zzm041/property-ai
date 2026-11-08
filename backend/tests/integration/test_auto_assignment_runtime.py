"""Auto-assignment runtime tests wait for 0009 assignment source columns."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


@pytest.fixture
def auto_assign_columns(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    columns = {item["name"] for item in inspector.get_columns("work_order_assignments")}
    if "assigned_source" not in columns:
        pytest.skip(
            "Migration 20260919_0009 assignment source columns are not applied"
        )


def test_auto_assign_schema_gate(auto_assign_columns: None) -> None:
    del auto_assign_columns
    assert True
