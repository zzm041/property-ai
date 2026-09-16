"""Task 6 Alembic revision chain, cycle, and drift checks on disposable MySQL."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.core.config import Settings
from app.core.database import assert_disposable_test_database

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TASK_4_REVISION = "20260916_0003"
TASK_5_REVISION = "20260916_0004"
TASK_6_REVISION = "20260917_0005"
TASK_7_REVISION = "20260917_0006"
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
TASK_6_TABLES = {
    "file_objects",
    "sla_policies",
    "work_orders",
    "work_order_contents",
    "work_order_media",
    "work_order_assignments",
    "work_order_logs",
    "work_order_processes",
    "work_order_receipts",
    "work_order_feedbacks",
    "sla_clocks",
    "idempotency_records",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_task_6_revision_chain_and_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    scripts = ScriptDirectory.from_config(
        _alembic_config(integration_settings.test_database_url)
    )
    task_6 = scripts.get_revision(TASK_6_REVISION)
    assert scripts.get_current_head() == TASK_7_REVISION
    assert task_6 is not None
    assert task_6.down_revision == TASK_5_REVISION
    assert _current_revision(test_engine) == TASK_7_REVISION


def test_task_6_alembic_has_no_model_drift(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    del test_engine
    command.check(_alembic_config(integration_settings.test_database_url))


def test_task_6_migration_cycle_preserves_task_1_to_5(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    assert_disposable_test_database(integration_settings)
    config = _alembic_config(integration_settings.test_database_url)
    assert _current_revision(test_engine) == TASK_7_REVISION

    command.downgrade(config, TASK_5_REVISION)
    assert _current_revision(test_engine) == TASK_5_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_6_TABLES.isdisjoint(tables)
    assert TASK_5_TABLES | {"houses", "users", "alembic_version"} <= tables
    with test_engine.connect() as connection:
        remaining = connection.execute(
            text(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN (
                    'work_orders',
                    'work_order_assignments',
                    'work_order_logs',
                    'file_objects',
                    'sla_policies',
                    'idempotency_records'
                  )
                """
            )
        ).all()
    assert remaining == []

    command.upgrade(config, TASK_7_REVISION)
    assert _current_revision(test_engine) == TASK_7_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_6_TABLES <= tables
    command.check(config)
