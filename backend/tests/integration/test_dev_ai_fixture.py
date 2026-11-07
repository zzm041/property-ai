"""Development AI fixture is idempotent and environment-guarded."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, DevSeedErrorCode
from app.model.employee import EmployeeSkill
from app.service.dev_ai_fixture import run_development_ai_fixture
from app.service.dev_employee_fixture import run_development_employee_fixture
from app.service.dev_house_fixture import run_development_house_fixture
from app.service.dev_seed import run_development_seed
from app.service.dev_work_order_fixture import run_development_work_order_fixture


def test_ai_fixture_requires_employee_and_is_idempotent(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    with pytest.raises(AppError) as missing:
        run_development_ai_fixture(
            integration_settings,
            engine_factory=lambda _url: test_engine,
        )
    assert missing.value.code == DevSeedErrorCode.DATA_CONFLICT

    run_development_seed(integration_settings, request_id="t7-fixture-seed")
    run_development_house_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    run_development_employee_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    run_development_work_order_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    first = run_development_ai_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    second = run_development_ai_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    identity_session.rollback()
    assert first.employee_id == second.employee_id
    assert first.plumbing_skill_id == second.plumbing_skill_id
    assert identity_session.scalar(select(func.count()).select_from(EmployeeSkill)) >= 2


def test_ai_fixture_production_guard_does_not_create_engine() -> None:
    factory = MagicMock()
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="non-default-production-secret-key",
        dev_auth_enabled=False,
        wechat_login_enabled=False,
    )
    with pytest.raises(AppError) as captured:
        run_development_ai_fixture(settings, engine_factory=factory)
    assert captured.value.code == DevSeedErrorCode.ENVIRONMENT_FORBIDDEN
    factory.assert_not_called()
