"""Task 7 Alembic revision chain, cycle, and drift checks."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.core.config import Settings
from app.core.database import assert_disposable_test_database

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TASK_6_REVISION = "20260917_0005"
TASK_7_REVISION = "20260917_0006"
TASK_8_REVISION = "20260917_0007"
TASK_6_TABLES = {
    "work_orders",
    "work_order_assignments",
    "file_objects",
    "sla_policies",
}
TASK_7_TABLES = {
    "work_order_analyses",
    "ai_call_logs",
    "dispatch_recommendations",
    "incidents",
    "incident_work_orders",
    "announcements",
    "announcement_scopes",
    "announcement_reviews",
    "notifications",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_task_7_revision_chain_and_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    scripts = ScriptDirectory.from_config(
        _alembic_config(integration_settings.test_database_url)
    )
    task_7 = scripts.get_revision(TASK_7_REVISION)
    assert scripts.get_current_head() == TASK_8_REVISION
    assert task_7 is not None
    assert task_7.down_revision == TASK_6_REVISION
    assert _current_revision(test_engine) == TASK_8_REVISION


def test_task_7_alembic_has_no_model_drift(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    del test_engine
    command.check(_alembic_config(integration_settings.test_database_url))


def test_task_7_migration_cycle_preserves_task_1_to_6(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    assert_disposable_test_database(integration_settings)
    config = _alembic_config(integration_settings.test_database_url)
    assert _current_revision(test_engine) == TASK_8_REVISION

    command.downgrade(config, TASK_6_REVISION)
    assert _current_revision(test_engine) == TASK_6_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_7_TABLES.isdisjoint(tables)
    assert TASK_6_TABLES <= tables
    with test_engine.connect() as connection:
        remaining = connection.execute(
            text(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN (
                    'work_order_analyses',
                    'ai_call_logs',
                    'incidents',
                    'announcements',
                    'notifications'
                  )
                """
            )
        ).all()
    assert remaining == []

    command.upgrade(config, TASK_8_REVISION)
    assert _current_revision(test_engine) == TASK_8_REVISION
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_7_TABLES <= tables
    command.check(config)
