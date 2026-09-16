"""Gate A Alembic revision-chain assertions."""

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.core.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TASK_2_REVISION = "20260915_0001"
TASK_3_REVISION = "20260915_0002"
TASK_4_REVISION = "20260916_0003"
TASK_5_REVISION = "20260916_0004"
TASK_6_REVISION = "20260917_0005"


def test_task_3_revision_is_independent_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = integration_settings.test_database_url
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision(TASK_3_REVISION)

    assert scripts.get_current_head() == TASK_6_REVISION
    assert revision is not None
    assert revision.down_revision == TASK_2_REVISION

    task_4 = scripts.get_revision(TASK_4_REVISION)
    assert task_4 is not None
    assert task_4.down_revision == TASK_3_REVISION

    task_5 = scripts.get_revision(TASK_5_REVISION)
    assert task_5 is not None
    assert task_5.down_revision == TASK_4_REVISION

    task_6 = scripts.get_revision(TASK_6_REVISION)
    assert task_6 is not None
    assert task_6.down_revision == TASK_5_REVISION

    with test_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    assert current == TASK_6_REVISION
