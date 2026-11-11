"""PA-174 / PA-177: the test-schema helper must refuse dangerous targets and repair half migrations.

PA-174: `PROTECTED_DATABASES` held no name containing "test", so the "is it protected"
branch was unreachable and `property_ai_test_staging` / `property_ai_prod_test` / a remote
`property_v2_test` all passed. PA-177: a schema with 64 tables and an empty
`alembic_version` died on `(1050, "Table 'tenants' already exists")` behind a 109-line
traceback, while `--check` told the operator to "run this script without --check".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[3]
BASE_URL = "mysql+pymysql://property_test:change-me-test@{host}/{db}?charset=utf8mb4"


_MODULE = None


def helper():
    """Load scripts/ops/prepare_test_database.py once, so exception classes stay identical."""

    global _MODULE
    if _MODULE is not None:
        return _MODULE
    spec = importlib.util.spec_from_file_location(
        "prepare_test_database", ROOT / "scripts/ops/prepare_test_database.py"
    )
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules[cls.__module__], so the
    # module has to be registered before exec_module or SchemaState() explodes.
    sys.modules["prepare_test_database"] = module
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def settings_for(host: str, database: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=BASE_URL.format(host="127.0.0.1", db="property_ai_scratch"),
        test_database_url=BASE_URL.format(host=host, db=database),
    )


# --- PA-174: name rules -------------------------------------------------------------


@pytest.mark.parametrize(
    "database",
    ["property_ai_test", "property_ai_test_2", "acme_orders_test", "property_test_scratch"],
)
def test_test_shaped_names_pass(database: str) -> None:
    helper().check_database_name(database)


@pytest.mark.parametrize(
    "database",
    [
        "property_ai_test_staging",  # reported in PA-174
        "property_ai_prod_test",  # reported in PA-174
        "property_ai_test_live",
        "property_ai_delivery_check",
        "property_ai_test_backup",
        "property_ai",  # no "test" at all
        "mysql",
    ],
)
def test_business_names_are_refused(database: str) -> None:
    module = helper()
    with pytest.raises(module.PreparationRefused):
        module.check_database_name(database)


def test_protected_branch_is_reachable_at_last() -> None:
    """The PA-174 bug: no protected name contained "test", so this branch never fired."""

    module = helper()
    assert module.PROTECTED_DATABASES, "the protected list must not be empty"
    with pytest.raises(module.PreparationRefused, match="protected database"):
        module.check_database_name("property_ai_delivery_check")


def test_protected_prefixes_are_configurable(monkeypatch) -> None:
    module = helper()
    monkeypatch.setenv("PREPARE_TEST_DB_PROTECTED_PREFIXES", "acme_")
    with pytest.raises(module.PreparationRefused, match="protected prefix"):
        module.check_database_name("acme_billing_test")


# --- PA-174: host allowlist ---------------------------------------------------------


def test_remote_hosts_are_refused(monkeypatch) -> None:
    module = helper()
    monkeypatch.delenv("PREPARE_TEST_DB_ALLOWED_HOSTS", raising=False)
    with pytest.raises(module.PreparationRefused, match="not a local test host"):
        module.resolve_target(settings_for("10.1.2.3", "property_v2_test"))


def test_remote_host_can_be_opted_in(monkeypatch) -> None:
    module = helper()
    monkeypatch.setenv("PREPARE_TEST_DB_ALLOWED_HOSTS", "10.1.2.3")
    assert module.resolve_target(settings_for("10.1.2.3", "property_v2_test")).endswith(
        "/property_v2_test?charset=utf8mb4"
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_loopback_hosts_pass(host: str) -> None:
    helper().resolve_target(settings_for(host, "property_ai_test"))


def test_development_database_is_refused() -> None:
    """Settings already rejects identical URLs; the helper keeps its own belt-and-braces check."""

    module = helper()
    same = BASE_URL.format(host="127.0.0.1", db="property_ai_test")
    with pytest.raises(Exception, match="different MySQL database"):
        Settings(_env_file=None, app_env="test", database_url=same, test_database_url=same)

    settings = Settings.model_construct(
        app_env="test", database_url=same, test_database_url=same
    )
    with pytest.raises(module.PreparationRefused, match="points at the development database"):
        module.resolve_target(settings)


def test_non_test_environment_is_refused() -> None:
    module = helper()
    settings = settings_for("127.0.0.1", "property_ai_test").model_copy(
        update={"app_env": "staging"}
    )
    with pytest.raises(module.PreparationRefused, match="APP_ENV must be test"):
        module.resolve_target(settings)


# --- PA-174: confirmation gate ------------------------------------------------------


class _Tty:
    def isatty(self) -> bool:
        return True


def test_confirmation_requires_flag_in_non_interactive_shell(monkeypatch) -> None:
    module = helper()

    class _Pipe:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(module.sys, "stdin", _Pipe())
    with pytest.raises(module.PreparationRefused, match="--yes-i-mean-it"):
        module.confirm_target("127.0.0.1:3307/property_ai_test", "property_ai_test", assume_yes=False)


def test_confirmation_accepts_the_typed_name(monkeypatch) -> None:
    module = helper()
    monkeypatch.setattr(module.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": "property_ai_test")
    module.confirm_target("127.0.0.1:3307/property_ai_test", "property_ai_test", assume_yes=False)


def test_confirmation_rejects_a_mismatch(monkeypatch) -> None:
    module = helper()
    monkeypatch.setattr(module.sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": "something_else")
    with pytest.raises(module.PreparationRefused, match="confirmation mismatch"):
        module.confirm_target("127.0.0.1:3307/property_ai_test", "property_ai_test", assume_yes=False)


def test_confirmation_survives_end_of_input(monkeypatch) -> None:
    """A detached terminal made `input()` raise EOFError, which escaped as a traceback."""

    module = helper()
    monkeypatch.setattr(module.sys, "stdin", _Tty())

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(module.PreparationRefused, match="no answer to the confirmation prompt"):
        module.confirm_target("127.0.0.1:3307/property_ai_test", "property_ai_test", assume_yes=False)


def test_flag_skips_the_prompt(monkeypatch) -> None:
    module = helper()

    def explode(prompt: str = "") -> str:
        raise AssertionError("--yes-i-mean-it must not prompt")

    monkeypatch.setattr("builtins.input", explode)
    module.confirm_target("127.0.0.1:3307/property_ai_test", "property_ai_test", assume_yes=True)


# --- PA-177: revision inference -----------------------------------------------------


def test_inference_covers_every_revision_table_set() -> None:
    module = helper()
    sets = module.revision_table_sets()
    order = module.revision_order()
    assert order[-1] == module.HEAD
    assert len(sets[module.HEAD]) == 63
    for revision, tables in sets.items():
        inferred, why = module.infer_revision(sorted(tables))
        assert inferred is not None, (revision, why)
        # An exact table set must infer a revision whose set is the same, never a smaller one.
        assert sets[inferred] == tables or len(sets[inferred]) >= len(tables)


def test_inference_of_a_head_shaped_schema_lands_on_head() -> None:
    module = helper()
    head_tables = sorted(module.revision_table_sets()[module.HEAD])
    revision, why = module.infer_revision(head_tables)
    assert revision == module.HEAD
    assert "table set matches" in why


def test_inference_of_a_smaller_schema_lands_earlier() -> None:
    module = helper()
    sets = module.revision_table_sets()
    partial = sorted(sets["20260925_0012_task_intents"])
    revision, _ = module.infer_revision(partial)
    assert revision == "20260925_0012_task_intents"


def test_unknown_tables_are_not_guessed() -> None:
    module = helper()
    revision, why = module.infer_revision(["mystery_table"])
    assert revision is None
    assert "no revision's table set is a subset" in why


# --- PA-177: messages match behaviour ----------------------------------------------


def state(module, tables: list[str], revision: str | None):
    return module.SchemaState(
        missing_markers=tuple(
            name for name in module.TEST_SCHEMA_MARKER_TABLES if name not in tables
        ),
        revision=revision,
        tables=tuple(tables),
    )


def test_check_text_for_an_empty_schema_promises_creation() -> None:
    module = helper()
    message = module.check_message("db", state(module, [], None), module.HEAD)
    assert message.startswith("TEST_DATABASE_SCHEMA_MISSING")
    assert "Re-running without --check creates it" in message


def test_check_text_for_a_half_migrated_schema_promises_a_stamp() -> None:
    module = helper()
    tables = sorted(module.revision_table_sets()[module.HEAD])
    message = module.check_message("db", state(module, tables, None), module.HEAD)
    assert message.startswith("TEST_DATABASE_SCHEMA_UNSTAMPED")
    assert f"stamps it at {module.HEAD}" in message


def test_check_text_for_an_uninferable_schema_does_not_promise_a_stamp() -> None:
    module = helper()
    message = module.check_message("db", state(module, ["mystery_table"], None), module.HEAD)
    assert message.startswith("TEST_DATABASE_SCHEMA_UNSTAMPED")
    assert "will refuse to guess" in message
    assert "--stamp" in message


def test_check_text_for_a_behind_schema_names_both_revisions() -> None:
    module = helper()
    tables = sorted(module.revision_table_sets()["20260925_0012_task_intents"])
    message = module.check_message("db", state(module, tables, "20260925_0012_task_intents"), module.HEAD)
    assert message.startswith("TEST_DATABASE_SCHEMA_MISSING")
    assert "is behind head" in message
    assert module.HEAD in message


def test_half_migrated_message_offers_two_executable_options() -> None:
    module = helper()
    tables = sorted(module.revision_table_sets()[module.HEAD])
    message = module.half_migrated_message(
        "127.0.0.1:3307/property_ai_test",
        state(module, tables, None),
        reason="no revision's table set is a subset of the tables found",
        known_revisions=module.revision_order(),
    )
    assert message.startswith("TEST_DATABASE_HALF_MIGRATED")
    assert "down -v mysql-test" in message
    assert "--stamp <revision>" in message
    assert module.HEAD in message


# --- main() wiring ------------------------------------------------------------------


def test_main_refuses_without_confirmation(monkeypatch, capsys) -> None:
    module = helper()
    monkeypatch.setenv(
        "TEST_DATABASE_URL", BASE_URL.format(host="127.0.0.1", db="property_ai_test")
    )
    monkeypatch.setenv(
        "DATABASE_URL", BASE_URL.format(host="127.0.0.1", db="property_ai_scratch")
    )
    monkeypatch.setenv("APP_ENV", "test")

    class _Pipe:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(module.sys, "stdin", _Pipe())

    def explode(target: str):
        raise AssertionError("the confirmation gate must run before any connection")

    monkeypatch.setattr(module, "schema_state", explode)
    assert module.main([]) == 2
    assert "TEST_DATABASE_REFUSED" in capsys.readouterr().out


def test_main_check_reports_unstamped_without_migrating(monkeypatch, capsys) -> None:
    module = helper()
    monkeypatch.setenv(
        "TEST_DATABASE_URL", BASE_URL.format(host="127.0.0.1", db="property_ai_test")
    )
    monkeypatch.setenv(
        "DATABASE_URL", BASE_URL.format(host="127.0.0.1", db="property_ai_scratch")
    )
    monkeypatch.setenv("APP_ENV", "test")
    tables = sorted(module.revision_table_sets()[module.HEAD])
    monkeypatch.setattr(module, "schema_state", lambda target: state(module, tables, None))

    def explode(*args, **kwargs):
        raise AssertionError("--check must never migrate")

    monkeypatch.setattr(module, "upgrade_to_head", explode)
    assert module.main(["--check"]) == 1
    assert "TEST_DATABASE_SCHEMA_UNSTAMPED" in capsys.readouterr().out
