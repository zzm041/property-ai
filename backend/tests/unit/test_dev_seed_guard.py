"""Development Seed environment guard, lock, and CLI safety tests."""

from unittest.mock import MagicMock

import pytest

from app.cli.dev_seed import main as dev_seed_main
from app.core.config import Settings
from app.core.errors import AppError, DevSeedErrorCode
from app.service.dev_seed import require_dev_seed_environment, run_development_seed


def _production_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="non-default-production-secret-key",
        dev_auth_enabled=False,
    )


def _staging_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="staging",
        app_secret_key="non-default-staging-secret-key",
        dev_auth_enabled=False,
    )


def _test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        dev_seed_lock_timeout_seconds=0,
    )


@pytest.mark.parametrize("settings_factory", [_production_settings, _staging_settings])
def test_guard_rejects_non_development_environments_before_engine(
    settings_factory,
) -> None:
    factory = MagicMock()
    session_factory = MagicMock()
    settings = settings_factory()

    with pytest.raises(AppError) as captured:
        run_development_seed(
            settings,
            engine_factory=factory,
            session_factory=session_factory,
        )

    assert captured.value.code == DevSeedErrorCode.ENVIRONMENT_FORBIDDEN
    factory.assert_not_called()
    session_factory.assert_not_called()
    require_dev_seed_environment(_test_settings())


def test_cli_production_and_staging_fail_before_engine_factory(
    monkeypatch,
) -> None:
    factory = MagicMock()
    monkeypatch.setattr("app.service.dev_seed.create_database_engine", factory)

    for settings in (_production_settings(), _staging_settings()):
        monkeypatch.setattr(
            "app.cli.dev_seed.get_settings",
            lambda current=settings: current,
        )
        assert dev_seed_main() == 1
        factory.assert_not_called()


def test_lock_timeout_does_not_create_session_or_write() -> None:
    engine = MagicMock()
    lock_connection = MagicMock()
    engine.connect.return_value.execution_options.return_value = lock_connection
    lock_connection.execute.return_value.scalar.return_value = 0
    session_factory = MagicMock()

    with pytest.raises(AppError) as captured:
        run_development_seed(
            _test_settings(),
            engine_factory=lambda url: engine,
            session_factory=session_factory,
        )

    assert captured.value.code == DevSeedErrorCode.LOCK_UNAVAILABLE
    session_factory.assert_not_called()
    engine.dispose.assert_called_once()
    lock_connection.close.assert_called_once()
    assert lock_connection.execute.call_count >= 2


def test_lock_null_result_fails_closed_without_session() -> None:
    engine = MagicMock()
    lock_connection = MagicMock()
    engine.connect.return_value.execution_options.return_value = lock_connection
    lock_connection.execute.return_value.scalar.return_value = None
    session_factory = MagicMock()

    with pytest.raises(AppError) as captured:
        run_development_seed(
            _test_settings(),
            engine_factory=lambda url: engine,
            session_factory=session_factory,
        )

    assert captured.value.code == DevSeedErrorCode.LOCK_UNAVAILABLE
    session_factory.assert_not_called()
    lock_connection.close.assert_called_once()


def test_release_lock_failure_still_closes_lock_connection() -> None:
    engine = MagicMock()
    lock_connection = MagicMock()
    engine.connect.return_value.execution_options.return_value = lock_connection
    lock_connection.execute.return_value.scalar.return_value = 0
    lock_connection.execute.side_effect = [
        MagicMock(scalar=lambda: 0),
        RuntimeError("forced release failure"),
    ]

    with pytest.raises(AppError) as captured:
        run_development_seed(
            _test_settings(),
            engine_factory=lambda url: engine,
        )

    assert captured.value.code == DevSeedErrorCode.LOCK_UNAVAILABLE
    lock_connection.close.assert_called_once()
    engine.dispose.assert_called_once()


def test_application_startup_does_not_import_dev_seed_cli() -> None:
    from pathlib import Path

    main_source = (
        Path(__file__).resolve().parents[2] / "app" / "main.py"
    ).read_text(encoding="utf-8")
    assert "dev_seed" not in main_source
    assert "app.cli" not in main_source
