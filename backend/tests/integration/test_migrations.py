"""Alembic head and destructive Test DB Guard tests."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.core.config import Settings
from app.core.database import assert_disposable_test_database

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def test_database_revision_on_shared_test_schema_stays_0007(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    config = _alembic_config(integration_settings.test_database_url)
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == ["20260919_0009"]

    with test_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    assert current == "20260917_0007"


def test_guard_rejects_development_environment() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        allow_destructive_test_database=True,
    )

    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        assert_disposable_test_database(
            settings,
            database_url=settings.test_database_url,
        )


def test_guard_requires_explicit_destructive_flag() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=False,
    )

    with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE"):
        assert_disposable_test_database(settings)


def test_guard_rejects_development_url() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
    )

    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        assert_disposable_test_database(
            settings,
            database_url=settings.database_url,
        )
