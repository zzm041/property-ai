"""Guarded disposable MySQL fixtures for Task 2 integration tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    assert_disposable_test_database,
    create_database_engine,
    get_session_factory,
    verify_utc_session,
)

TASK_2_TABLES = ("tenants", "properties", "buildings", "units", "houses")


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
    )


@pytest.fixture(scope="session")
def test_engine(integration_settings: Settings) -> Iterator[Engine]:
    assert_disposable_test_database(integration_settings)
    engine = create_database_engine(integration_settings.test_database_url)
    with engine.connect() as connection:
        verify_utc_session(connection)
        version = str(connection.execute(text("SELECT VERSION()")).scalar_one())
        if not version.startswith("8."):
            raise RuntimeError("Task 2 integration tests require MySQL 8")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def clean_property_tables(test_engine: Engine) -> Iterator[None]:
    missing = [
        table for table in TASK_2_TABLES if not inspect(test_engine).has_table(table)
    ]
    if missing:
        raise RuntimeError(
            "Task 2 migration must be upgraded before integration tests: "
            + ", ".join(missing)
        )

    def clean() -> None:
        with test_engine.begin() as connection:
            for table in reversed(TASK_2_TABLES):
                connection.execute(text(f"DELETE FROM `{table}`"))

    clean()
    try:
        yield
    finally:
        clean()


@pytest.fixture
def db_session(
    integration_settings: Settings,
    clean_property_tables: None,
) -> Iterator[Session]:
    del clean_property_tables
    session = get_session_factory(integration_settings.test_database_url)()
    try:
        yield session
    finally:
        session.close()
