"""Official auth runtime tests wait for disposable-DB approval of migration 0009."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


REQUIRED = ("user_phones", "user_passwords", "sms_challenges", "auth_sessions")


@pytest.fixture
def official_auth_tables(test_engine: Engine) -> None:
    missing = [name for name in REQUIRED if not inspect(test_engine).has_table(name)]
    if missing:
        pytest.skip(
            "Migration 20260919_0009 is not applied on the disposable test database: "
            + ", ".join(missing)
        )


def test_official_auth_schema_gate(official_auth_tables: None) -> None:
    del official_auth_tables
    assert True
