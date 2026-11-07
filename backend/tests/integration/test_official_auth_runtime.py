"""Official auth runtime tests on shared property_ai_test must not skip-as-PASS."""

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
    missing = [name for name in REQUIRED if not inspect(test_engine).has_table(name)]
    assert missing == list(REQUIRED)
