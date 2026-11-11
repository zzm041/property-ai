"""PA-175 (t182): ``alembic check`` must stay clean on a migrated schema.

``alembic check`` failed on head with four classes of drift, all of them
dangerous because autogenerate would have offered to *drop* audit columns and
indexes to "fix" them.  The migration files are the source of truth, so the
fixes lived on the model side; this test keeps them honest by comparing the
live schema against ``Base.metadata`` the same way ``alembic check`` does.
"""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from app.model.base import Base


def test_schema_matches_the_models(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        diff = compare_metadata(
            MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            ),
            Base.metadata,
        )

    # An empty list is what ``alembic check`` prints as
    # "No new upgrade operations detected."
    assert diff == []
