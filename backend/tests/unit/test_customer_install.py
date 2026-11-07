"""Customer install planning/authority tests; no database or service is started."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from app.cli import customer_install
from app.cli.customer_install_authority import CustomerInstallAuthority, permits_customer_install


def settings(database="client_property", host="client-mysql", env="production"):
    return SimpleNamespace(app_env=env, active_database_url=f"mysql+pymysql://installer:PRIVATE_VALUE@{host}:3306/{database}")


def test_plan_is_offline_contains_no_credentials_and_binds_source(monkeypatch):
    monkeypatch.setattr(customer_install, "create_database_engine", MagicMock(side_effect=AssertionError("unexpected database")))
    plan = customer_install.build_plan(settings())
    assert "PRIVATE_VALUE" not in json.dumps(plan)
    assert plan["target"] == "client-mysql:3306/client_property"
    assert plan["head"] == "20261003_0015_work_order_media_cycle_links"
    assert len(plan["source_sha256"]) == 64


@pytest.mark.parametrize("name", ["mysql", "sys", "information_schema", "performance_schema", "property_ai_delivery_check", "property_ai", "property_ai_auth001_test", "bad-name", ""])
def test_plan_rejects_protected_or_invalid_database(name):
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_TARGET"):
        customer_install.build_plan(settings(database=name))


@pytest.mark.parametrize("change", ["target", "head", "source_sha256", "app_env", "unexpected"])
def test_changed_plan_is_rejected_before_connect(monkeypatch, change):
    current = settings()
    plan = customer_install.build_plan(current)
    plan[change] = "changed"
    connect = MagicMock()
    monkeypatch.setattr(customer_install, "create_database_engine", connect)
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_PLAN"):
        customer_install.apply_plan(current, plan, customer_install.plan_digest(plan))
    connect.assert_not_called()


def test_missing_operator_digest_cannot_execute(monkeypatch):
    plan = customer_install.build_plan(settings())
    connect = MagicMock()
    monkeypatch.setattr(customer_install, "create_database_engine", connect)
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_APPROVAL"):
        customer_install.apply_plan(settings(), plan, "")
    connect.assert_not_called()


@pytest.mark.parametrize("query", ["unix_socket=/tmp/other.sock", "host=other-server", "database=other_schema"])
def test_url_query_cannot_override_the_reviewed_target(query):
    current = settings()
    current.active_database_url += "?" + query
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_TARGET"):
        customer_install.build_plan(current)


def test_missing_bootstrap_credentials_refuses_before_any_connection(monkeypatch):
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PHONE", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    connect = MagicMock()
    monkeypatch.setattr(customer_install, "create_database_engine", connect)
    plan = customer_install.build_plan(settings())
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_ADMIN"):
        customer_install.apply_plan(settings(), plan, customer_install.plan_digest(plan))
    connect.assert_not_called()


def test_nonempty_database_is_refused_without_ddl():
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = 1
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_NOT_EMPTY"):
        customer_install.require_empty_database(connection)
    assert all("CREATE" not in str(call) and "ALTER" not in str(call) for call in connection.execute.call_args_list)


def test_authority_binds_same_live_connection_target_and_revision(monkeypatch):
    bind = SimpleNamespace(engine=SimpleNamespace(url=make_url(settings().active_database_url)))
    authority = CustomerInstallAuthority(bind, "client-mysql:3306/client_property", frozenset({"rev1"}))
    from alembic import context
    monkeypatch.setattr(context, "config", SimpleNamespace(attributes={"customer_install_authority": authority}), raising=False)
    assert permits_customer_install(bind, "rev1")
    assert not permits_customer_install(bind, "rev2")
    other = SimpleNamespace(engine=bind.engine)
    assert not permits_customer_install(other, "rev1")
    authority.active = False
    assert not permits_customer_install(bind, "rev1")


def test_ordinary_alembic_has_no_customer_authority(monkeypatch):
    from alembic import context
    monkeypatch.setattr(context, "config", SimpleNamespace(attributes={}), raising=False)
    assert not permits_customer_install(MagicMock(), "rev1")


def install_fixture(monkeypatch, *, busy=False, fail_revision=None):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PHONE", "+8613900000000")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "synthetic-install-only-42")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_DISPLAY_NAME", "Synthetic Admin")
    current = settings()
    connection = MagicMock()
    connection.invalidated = False
    connection.engine.url = make_url(current.active_database_url)
    connection.__enter__.return_value = connection
    statements = []

    def execute(statement, parameters=None):
        sql = str(statement)
        statements.append(sql)
        result = MagicMock()
        if "GET_LOCK" in sql:
            result.scalar_one.return_value = 0 if busy else 1
        elif "SELECT DATABASE()" in sql:
            result.scalar_one.return_value = "client_property"
        elif "information_schema" in sql:
            result.scalar_one.return_value = 0
        elif "SELECT version_num" in sql:
            result.scalar_one.return_value = customer_install.HEAD
        return result

    connection.execute.side_effect = execute
    engine = MagicMock()
    engine.connect.return_value = connection
    monkeypatch.setattr(customer_install, "create_database_engine", lambda _: engine)
    sequence = []
    authorities = []

    def upgrade(config, revision):
        assert config.attributes["connection"] is connection
        authority = config.attributes["customer_install_authority"]
        assert authority.active
        assert authority.connection is connection
        authorities.append(authority)
        sequence.append(revision)
        if fail_revision == revision:
            raise RuntimeError("synthetic migration failure")

    def bootstrap(*args, **kwargs):
        assert kwargs["connection"] is connection
        sequence.append("bootstrap")

    monkeypatch.setattr(customer_install.command, "upgrade", upgrade)
    monkeypatch.setattr(customer_install, "_bootstrap", bootstrap)
    return current, engine, statements, sequence, authorities


def test_install_sequence_holds_authority_only_during_install(monkeypatch):
    current, engine, sql, sequence, authorities = install_fixture(monkeypatch)
    plan = customer_install.build_plan(current)
    customer_install.apply_plan(current, plan, customer_install.plan_digest(plan))
    assert sequence == [customer_install.PRE_BOOTSTRAP, "bootstrap", customer_install.IDENTITY, customer_install.HEAD]
    assert all(not authority.active for authority in authorities)
    assert "GET_LOCK" in sql[0]
    assert "RELEASE_LOCK" in sql[-1]
    assert not any("DROP" in statement or "TRUNCATE" in statement for statement in sql)
    engine.dispose.assert_called_once()


def test_busy_installer_refuses_without_writes(monkeypatch):
    current, engine, sql, sequence, _ = install_fixture(monkeypatch, busy=True)
    plan = customer_install.build_plan(current)
    with pytest.raises(ValueError, match="CUSTOMER_INSTALL_BUSY"):
        customer_install.apply_plan(current, plan, customer_install.plan_digest(plan))
    assert sequence == []
    assert len(sql) == 1 and "GET_LOCK" in sql[0]
    engine.dispose.assert_called_once()


def test_mid_install_failure_revokes_authority_releases_lock_and_preserves_database(monkeypatch):
    current, engine, sql, sequence, authorities = install_fixture(monkeypatch, fail_revision=customer_install.IDENTITY)
    plan = customer_install.build_plan(current)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        customer_install.apply_plan(current, plan, customer_install.plan_digest(plan))
    assert customer_install.HEAD not in sequence
    assert all(not authority.active for authority in authorities)
    assert "RELEASE_LOCK" in sql[-1]
    assert not any("DROP" in statement or "TRUNCATE" in statement for statement in sql)
    engine.dispose.assert_called_once()
