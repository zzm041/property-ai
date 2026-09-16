"""Development employee fixture is separate from IAM Seed and environment-guarded."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, DevSeedErrorCode
from app.model.employee import (
    AttendanceRecord,
    Department,
    Duty,
    Employee,
    EmployeeDailyStatus,
    EmployeeProperty,
    Skill,
)
from app.service.dev_employee_fixture import (
    DEV_DEPARTMENT_CODE,
    DEV_EMPLOYEE_NO,
    run_development_employee_fixture,
)
from app.service.dev_seed import run_development_seed
from app.model.work_order import WorkOrder


def test_employee_fixture_requires_seed_and_is_idempotent(
    identity_session: Session,
    integration_settings: Settings,
    test_engine: Engine,
) -> None:
    with pytest.raises(AppError) as missing:
        run_development_employee_fixture(
            integration_settings,
            engine_factory=lambda _url: test_engine,
        )
    assert missing.value.code == DevSeedErrorCode.DATA_CONFLICT

    run_development_seed(integration_settings, request_id="t5-fixture-seed")
    first = run_development_employee_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    second = run_development_employee_fixture(
        integration_settings,
        engine_factory=lambda _url: test_engine,
    )
    identity_session.rollback()
    assert first.employee_id == second.employee_id
    assert first.assignment_id == second.assignment_id
    assert identity_session.scalar(select(func.count()).select_from(Employee)) == 1
    assert identity_session.scalar(select(func.count()).select_from(Department)) == 1
    assert identity_session.scalar(select(func.count()).select_from(Duty)) == 3
    assert identity_session.scalar(select(func.count()).select_from(Skill)) == 3
    assert (
        identity_session.scalar(select(func.count()).select_from(EmployeeProperty))
        == 1
    )
    assert (
        identity_session.scalar(select(func.count()).select_from(AttendanceRecord))
        == 0
    )
    assert (
        identity_session.scalar(select(func.count()).select_from(EmployeeDailyStatus))
        == 0
    )
    employee = identity_session.get(Employee, first.employee_id)
    assert employee is not None
    assert employee.employee_no == DEV_EMPLOYEE_NO
    department = identity_session.get(Department, first.department_id)
    assert department is not None
    assert department.code == DEV_DEPARTMENT_CODE
    assert identity_session.scalar(select(func.count()).select_from(WorkOrder)) == 0


def test_employee_fixture_production_guard_does_not_create_engine() -> None:
    factory = MagicMock()
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="non-default-production-secret-key",
        dev_auth_enabled=False,
    )
    with pytest.raises(AppError) as captured:
        run_development_employee_fixture(settings, engine_factory=factory)
    assert captured.value.code == DevSeedErrorCode.ENVIRONMENT_FORBIDDEN
    factory.assert_not_called()
