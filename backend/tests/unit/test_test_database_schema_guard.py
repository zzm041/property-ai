"""PA-118: an empty test schema must fail once, readably, instead of 200+ times.

The regression this pins: `APP_ENV=test` pointed at the disposable `property_ai_test`
database that `deploy/docker-compose.yml` creates empty (no migration). Integration
tests then failed one at a time with `Table 'property_ai_test.users' doesn't exist`,
and API-level tests answered 500 `SYS.INTERNAL_ERROR`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import app.core.database as database
from app.core.config import Settings
from app.core.database import (
    TEST_SCHEMA_MARKER_TABLES,
    TestDatabaseSchemaError,
    missing_test_schema_tables,
    verify_test_database_schema,
)

TEST_URL = (
    "mysql+pymysql://property_test:change-me-test"
    "@127.0.0.1:3399/property_ai_test?charset=utf8mb4"
)

#: Settings refuses `production` without production credentials; the guard only reads
#: `app_env` / `require_test_database_schema`, so build the baseline and override.
BASELINE = Settings(_env_file=None, app_env="test")


def _settings(**overrides) -> Settings:
    return BASELINE.model_copy(update=overrides)


@pytest.fixture()
def sqlite_engine(tmp_path, monkeypatch):
    """Point the guard at SQLite: it is a real engine, and no MySQL server is needed."""

    url = f"sqlite:///{tmp_path / 'schema.db'}"
    monkeypatch.setattr(
        database, "create_database_engine", lambda target, **kwargs: create_engine(url)
    )
    return create_engine(url)


def _create_markers(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))


def test_marker_tables_are_the_minimum_of_a_migrated_schema() -> None:
    assert set(TEST_SCHEMA_MARKER_TABLES) == {"alembic_version", "users"}


def test_missing_tables_are_listed(sqlite_engine) -> None:
    with sqlite_engine.connect() as connection:
        assert missing_test_schema_tables(connection) == ["alembic_version", "users"]


def test_missing_tables_are_empty_once_the_markers_exist(sqlite_engine) -> None:
    _create_markers(sqlite_engine)
    with sqlite_engine.connect() as connection:
        assert missing_test_schema_tables(connection) == []


def test_empty_schema_raises_one_named_error(sqlite_engine) -> None:
    with pytest.raises(TestDatabaseSchemaError) as caught:
        verify_test_database_schema(_settings(), database_url=TEST_URL)

    message = str(caught.value)
    assert message.startswith(
        "TEST_DATABASE_SCHEMA_MISSING: APP_ENV=test uses 127.0.0.1:3399/property_ai_test"
    )
    assert "has no application schema (missing tables: alembic_version, users)" in message
    # The message has to name the fix, because the failure it replaces named nothing.
    assert "python scripts/ops/prepare_test_database.py" in message
    assert "docs/runbook.md section 5" in message


def test_ready_schema_is_accepted(sqlite_engine) -> None:
    _create_markers(sqlite_engine)
    verify_test_database_schema(_settings(), database_url=TEST_URL)


def test_unreachable_database_names_the_container_command(monkeypatch) -> None:
    def refuse(target: str, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database, "create_database_engine", refuse)

    with pytest.raises(TestDatabaseSchemaError) as caught:
        verify_test_database_schema(_settings(), database_url=TEST_URL)

    message = str(caught.value)
    assert message.startswith(
        "TEST_DATABASE_UNREACHABLE: APP_ENV=test uses 127.0.0.1:3399/property_ai_test"
    )
    assert "--profile test up -d mysql-test" in message
    assert "python scripts/ops/prepare_test_database.py" in message


@pytest.mark.parametrize("environment", ["development", "staging", "production"])
def test_other_environments_are_not_checked(environment: str) -> None:
    """Only APP_ENV=test owns a disposable schema; everywhere else has readiness probes."""

    def explode(target: str, **kwargs):
        raise AssertionError("non-test environments must not touch the test database")

    original = database.create_database_engine
    database.create_database_engine = explode
    try:
        verify_test_database_schema(_settings(app_env=environment), database_url=TEST_URL)
    finally:
        database.create_database_engine = original


def test_check_can_be_switched_off_explicitly() -> None:
    def explode(target: str, **kwargs):
        raise AssertionError("the check is disabled; it must not connect")

    original = database.create_database_engine
    database.create_database_engine = explode
    try:
        verify_test_database_schema(
            _settings(require_test_database_schema=False), database_url=TEST_URL
        )
    finally:
        database.create_database_engine = original


def test_create_app_refuses_to_build_on_an_empty_test_schema(monkeypatch) -> None:
    """The guard has to be wired into startup, not just available as a function."""

    from app.main import create_app

    def refuse(target: str, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database, "create_database_engine", refuse)

    with pytest.raises(TestDatabaseSchemaError):
        create_app(_settings())
