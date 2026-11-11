"""Development house fixture is separate from IAM Seed and environment-guarded."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, DevSeedErrorCode
from app.model.employee import Employee
from app.model.property import Building, House, Unit
from app.service.dev_house_fixture import (
    DEV_BUILDING_CODE,
    DEV_HOUSE_ROOM_NO,
    DEV_UNIT_CODE,
    run_development_house_fixture,
)
from app.service.dev_seed import run_development_seed


def test_house_fixture_requires_seed_and_is_idempotent(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    with pytest.raises(AppError) as missing:
        run_development_house_fixture(
            integration_settings,
            engine_factory=lambda _url: test_engine,
        )
    assert missing.value.code == DevSeedErrorCode.DATA_CONFLICT

    run_development_seed(integration_settings, request_id="t4-fixture-seed")
    first = run_development_house_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    second = run_development_house_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    identity_session.rollback()
    assert first.house_id == second.house_id
    assert first.building_id == second.building_id
    assert identity_session.scalar(select(func.count()).select_from(Building)) == 1
    assert identity_session.scalar(select(func.count()).select_from(Unit)) == 1
    assert identity_session.scalar(select(func.count()).select_from(House)) == 1
    house = identity_session.get(House, first.house_id)
    assert house is not None
    assert house.room_no == DEV_HOUSE_ROOM_NO
    building = identity_session.get(Building, first.building_id)
    assert building is not None
    assert building.code == DEV_BUILDING_CODE
    unit = identity_session.get(Unit, first.unit_id)
    assert unit is not None
    assert     unit.code == DEV_UNIT_CODE
    assert identity_session.scalar(select(func.count()).select_from(Employee)) == 0


def test_house_fixture_production_guard_does_not_create_engine() -> None:
    factory = MagicMock()
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="non-default-production-secret-key",
        dev_auth_enabled=False,
        wechat_login_enabled=False,
    )
    with pytest.raises(AppError) as captured:
        run_development_house_fixture(settings, engine_factory=factory)
    assert captured.value.code == DevSeedErrorCode.ENVIRONMENT_FORBIDDEN
    factory.assert_not_called()
