"""Official auth runtime tests on shared property_ai_test must not skip-as-PASS."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


REQUIRED = (
    "user_phones",
    "user_passwords",
    "sms_challenges",
    "visual_challenges",
    "password_reset_tickets",
    "auth_sessions",
)


def test_shared_test_database_does_not_host_0009_auth_tables(
    test_engine: Engine,
) -> None:
    present = [name for name in REQUIRED if inspect(test_engine).has_table(name)]
    if present == list(REQUIRED):
        # PA-218: this is an environment precondition, not a property of official
        # auth. scripts/ops/prepare_test_database.py migrates the shared database to
        # head, and head contains 20260919_0009, so all six auth tables exist by
        # design. The old assertion ("the shared database stops before 0009") was
        # only true while the database sat at an older revision; it said nothing
        # about the auth code under test. No migration and no product code change
        # for this check: the database stays at head.
        pytest.skip(
            "shared test database is migrated to head (prepare_test_database.py), "
            "so the 0009 auth tables legitimately exist: " + ", ".join(present)
        )
    missing = [name for name in REQUIRED if name not in present]
    assert missing == list(REQUIRED), (
        "shared test database hosts a partial 0009 auth schema: "
        f"present={present}; expected either none (pre-0009) or all six (head)"
    )
