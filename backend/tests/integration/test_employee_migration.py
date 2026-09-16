"""Task 5 Alembic revision chain, cycle, and drift checks on disposable MySQL."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.core.config import Settings
from app.core.database import assert_disposable_test_database

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TASK_2_REVISION = "20260915_0001"
TASK_3_REVISION = "20260915_0002"
TASK_4_REVISION = "20260916_0003"
TASK_5_REVISION = "20260916_0004"
TASK_2_TABLES = {"tenants", "properties", "buildings", "units", "houses"}
TASK_3_TABLES = {
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "audit_logs",
}
TASK_4_TABLES = {"resident_bind_requests", "resident_house_relations"}
TASK_5_TABLES = {
    "departments",
    "duties",
    "skills",
    "employees",
    "employee_properties",
    "employee_duties",
    "employee_skills",
    "attendance_records",
    "employee_daily_statuses",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_task_5_revision_chain_and_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    scripts = ScriptDirectory.from_config(
        _alembic_config(integration_settings.test_database_url)
    )
    task_5 = scripts.get_revision(TASK_5_REVISION)
    task_4 = scripts.get_revision(TASK_4_REVISION)
    task_3 = scripts.get_revision(TASK_3_REVISION)
    task_2 = scripts.get_revision(TASK_2_REVISION)

    assert scripts.get_current_head() == TASK_5_REVISION
    assert task_2 is not None and task_2.down_revision is None
    assert task_3 is not None and task_3.down_revision == TASK_2_REVISION
    assert task_4 is not None and task_4.down_revision == TASK_3_REVISION
    assert task_5 is not None and task_5.down_revision == TASK_4_REVISION
    assert _current_revision(test_engine) == TASK_5_REVISION


def test_task_5_alembic_has_no_model_drift(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    del test_engine
    command.check(_alembic_config(integration_settings.test_database_url))


def test_task_5_migration_cycle_preserves_task_1_to_4(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    assert_disposable_test_database(integration_settings)
    config = _alembic_config(integration_settings.test_database_url)
    assert _current_revision(test_engine) == TASK_5_REVISION

    command.downgrade(config, TASK_4_REVISION)
    assert _current_revision(test_engine) == TASK_4_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_5_TABLES.isdisjoint(tables)
    assert TASK_2_TABLES | TASK_3_TABLES | TASK_4_TABLES | {"alembic_version"} <= tables
    with test_engine.connect() as connection:
        remaining = connection.execute(
            text(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN (
                    'employees',
                    'employee_properties',
                    'attendance_records',
                    'employee_daily_statuses',
                    'departments',
                    'duties',
                    'skills'
                  )
                """
            )
        ).all()
    assert remaining == []

    command.upgrade(config, TASK_5_REVISION)
    assert _current_revision(test_engine) == TASK_5_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_5_TABLES <= tables
    command.check(config)
