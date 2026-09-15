"""Configuration validation tests."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_and_test_databases_are_isolated() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url != settings.test_database_url
    assert "property_ai" in settings.database_url
    assert "property_ai_test" in settings.test_database_url


def test_test_environment_uses_disposable_database() -> None:
    settings = Settings(_env_file=None, app_env="test")

    assert settings.active_database_url == settings.test_database_url


def test_equal_database_targets_are_rejected() -> None:
    url = "mysql+pymysql://one:secret@127.0.0.1:3306/shared"

    with pytest.raises(ValidationError, match="different MySQL database"):
        Settings(_env_file=None, database_url=url, test_database_url=url)


def test_non_pymysql_database_driver_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must use mysql\\+pymysql"):
        Settings(
            _env_file=None,
            database_url="sqlite:///development.db",
        )
