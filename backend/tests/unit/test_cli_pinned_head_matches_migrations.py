"""Guard: both CLIs pin the single alembic head, never an intermediate revision.

The 0015 incident (t96 / D1 review) happened because `customer_install.HEAD` and
`delivery_migrate.HEAD` were left pointing at 0015 after 0016 was added: the
customer install then "succeeded" without the scoped idempotency constraint, and
the delivery migrator printed "migrated" while still sitting one revision behind.
These tests read the migration graph offline (no database, no `alembic upgrade`),
so adding a revision without moving the pins fails immediately.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.cli import customer_install, delivery_migrate


ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = ROOT / "backend" / "alembic.ini"
VERSIONS = ROOT / "backend" / "alembic" / "versions"


def script_directory() -> ScriptDirectory:
    # `alembic.ini` resolves `script_location` through %(here)s, i.e. backend/alembic.
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def test_the_migration_graph_has_exactly_one_head() -> None:
    heads = script_directory().get_heads()
    assert len(heads) == 1, f"expected a single linear head, got {heads}"


def test_both_clis_pin_the_alembic_head() -> None:
    head = script_directory().get_heads()[0]
    assert customer_install.HEAD == head
    assert delivery_migrate.HEAD == head


def test_customer_install_plan_reports_the_head() -> None:
    from types import SimpleNamespace

    settings = SimpleNamespace(
        app_env="production",
        active_database_url="mysql+pymysql://installer:secret@client-mysql:3306/client_property",
    )
    plan = customer_install.build_plan(settings)
    assert plan["head"] == script_directory().get_heads()[0]


def test_the_pinned_head_is_a_leaf_and_resumable_from_its_parent() -> None:
    script = script_directory()
    head = script.get_revision(customer_install.HEAD)
    # A leaf: nothing upgrades past it.
    assert [revision.revision for revision in script.get_revisions(head.revision)] == [head.revision]
    assert not any(
        other.down_revision == head.revision
        for other in script.walk_revisions()
        if other.revision != head.revision
    )
    # The delivery migrator must accept the immediate predecessor as a starting
    # point, otherwise a database parked one revision behind can never be resumed.
    assert delivery_migrate.MEDIA_CYCLE_REVISION == head.down_revision
