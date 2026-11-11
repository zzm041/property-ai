"""PA-175 (t182): the ORM models must declare everything the schema has.

``alembic check`` reported DIRTY for four classes of drift: audit columns the
models never declared (``ai_call_logs.provider_mode``,
``work_order_assignments.payload_json``/``assigned_source``), a nullability and
two integer-width mismatches, server defaults only the database had, and two
indexes only the database had.  Autogenerate turns every one of those into a
destructive ``drop_column`` / ``drop_index`` / ``alter_column`` operation, so
the guard here is cheap and DB-free: it pins the model side of each drifted
object.  The end-to-end check (``compare_metadata`` returning an empty diff) is
``tests/integration/test_schema_matches_models.py`` because it needs a migrated
database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, Integer, String

from app.model.base import Base


def _column(table_name: str, column_name: str):  # noqa: ANN202 - test helper
    return Base.metadata.tables[table_name].columns[column_name]


def _index_names(table_name: str) -> set[str]:
    return {index.name for index in Base.metadata.tables[table_name].indexes}


def test_ai_call_logs_declares_provider_mode() -> None:
    column = _column("ai_call_logs", "provider_mode")

    assert isinstance(column.type, String)
    assert column.type.length == 16
    assert column.nullable is False
    # 20260919_0009 backfilled existing rows, so the default belongs to the model too.
    assert column.server_default is not None
    assert "DEVELOPMENT" in str(column.server_default.arg)
    checks = " ".join(
        str(constraint.sqltext)
        for constraint in Base.metadata.tables["ai_call_logs"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    )
    assert "provider_mode" in checks


def test_work_order_assignments_declares_the_source_columns() -> None:
    assigned_source = _column("work_order_assignments", "assigned_source")
    assert isinstance(assigned_source.type, String)
    assert assigned_source.type.length == 16
    assert assigned_source.nullable is False
    assert "HUMAN" in str(assigned_source.server_default.arg)

    payload_json = _column("work_order_assignments", "payload_json")
    assert isinstance(payload_json.type, JSON)
    assert payload_json.nullable is True

    # AUTO assignments have no acting user, so 0009 dropped the NOT NULL.
    actor = _column("work_order_assignments", "assigned_by_user_id")
    assert actor.nullable is True


def test_employee_invite_counters_are_integers() -> None:
    for column_name in ("attempts", "max_attempts"):
        column = _column("employee_invites", column_name)
        assert isinstance(column.type, Integer), column_name
        assert column.nullable is False
        assert column.server_default is not None


def test_identity_recovery_status_carries_its_server_default() -> None:
    column = _column("identity_recovery_cases", "status")

    assert isinstance(column.type, String)
    assert column.type.length == 24
    assert column.nullable is False
    assert column.server_default is not None
    assert "PENDING_PROOF" in str(column.server_default.arg)


def test_identity_tables_keep_their_search_indexes() -> None:
    assert _index_names("account_merges") >= {"ix_account_merges_status", "ix_account_merges_target"}
    assert _index_names("identity_recovery_reviews") >= {"ix_identity_recovery_reviews_case"}
    assert _index_names("phone_identity_tombstones") >= {
        "ix_phone_tombstones_due",
        "ix_phone_tombstones_history",
    }
    assert _index_names("identity_recovery_cases") >= {
        "ix_identity_recovery_source",
        "ix_identity_recovery_status_expiry",
    }


def test_remaining_server_defaults_match_the_migration() -> None:
    expected = {
        ("identity_recovery_cases", "attempt_count"): "0",
        ("identity_recovery_cases", "version"): "1",
        ("phone_identity_tombstones", "status"): "QUARANTINED",
        ("account_merges", "status"): "COMPLETED",
    }
    for (table_name, column_name), default in expected.items():
        column = _column(table_name, column_name)
        assert column.server_default is not None, (table_name, column_name)
        assert default in str(column.server_default.arg), (table_name, column_name)
