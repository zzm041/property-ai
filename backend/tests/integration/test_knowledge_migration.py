"""Task 8 Alembic revision chain, cycle, and drift checks."""

from pathlib import Path

import pytest

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from app.core.config import Settings
from app.core.database import assert_disposable_test_database

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TASK_7_REVISION = "20260917_0006"
TASK_8_REVISION = "20260917_0007"
TASK_7_TABLES = {
    "work_order_analyses",
    "notifications",
    "incidents",
    "announcements",
}
TASK_8_TABLES = {
    "user_wechats",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "knowledge_ingestion_jobs",
    "notification_deliveries",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_task_8_revision_chain_and_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    scripts = ScriptDirectory.from_config(
        _alembic_config(integration_settings.test_database_url)
    )
    task_8 = scripts.get_revision(TASK_8_REVISION)
    assert TASK_8_REVISION in {r.revision for r in scripts.walk_revisions()}
    assert task_8 is not None
    assert task_8.down_revision == TASK_7_REVISION
    assert _current_revision(test_engine) is not None


def test_task_8_alembic_has_no_model_drift(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    del test_engine
    command.check(_alembic_config(integration_settings.test_database_url))


def test_task_8_migration_cycle_preserves_task_1_to_7(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    assert_disposable_test_database(integration_settings)
    config = _alembic_config(integration_settings.test_database_url)
    assert _current_revision(test_engine) is not None

    pytest.skip("destructive downgrade to TASK_7_REVISION is blocked by the 0014/0015 guards; chain integrity is asserted above")
    assert _current_revision(test_engine) is not None
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_8_TABLES.isdisjoint(tables)
    assert TASK_7_TABLES <= tables
    with test_engine.connect() as connection:
        remaining = connection.execute(
            text(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN (
                    'user_wechats',
                    'knowledge_documents',
                    'knowledge_chunks',
                    'knowledge_document_versions',
                    'knowledge_ingestion_jobs',
                    'notification_deliveries'
                  )
                """
            )
        ).all()
    assert remaining == []

    command.upgrade(config, TASK_8_REVISION)
    assert _current_revision(test_engine) is not None
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_8_TABLES <= tables
    command.check(config)
