"""Guarded disposable MySQL fixtures for Task 2 integration tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    assert_disposable_test_database,
    create_database_engine,
    get_session_factory,
    verify_test_database_schema,
    verify_utc_session,
)

TASK_2_TABLES = ("tenants", "properties", "buildings", "units", "houses")
TASK_3_TABLES = (
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "audit_logs",
)
TASK_4_TABLES = (
    "resident_house_relations",
    "resident_bind_requests",
)
TASK_5_TABLES = (
    "employee_daily_statuses",
    "attendance_records",
    "employee_skills",
    "employee_duties",
    "employee_properties",
    "employees",
    "departments",
    "duties",
    "skills",
)
TASK_6_TABLES = (
    # 0015 binds work_order_media to its process/receipt cycle through composite
    # foreign keys, so media rows must go before the cycle rows they reference.
    "work_order_media",
    "work_order_sla_applications",
    "work_order_feedbacks",
    "work_order_receipts",
    "work_order_processes",
    "work_order_logs",
    "work_order_assignments",
    "sla_clocks",
    "work_order_contents",
    "work_orders",
    "sla_policies",
    "file_objects",
    "idempotency_records",
)
TASK_7_TABLES = (
    "notifications",
    "announcement_reviews",
    "announcement_scopes",
    "announcements",
    "dispatch_recommendations",
    "ai_call_logs",
    "work_order_analyses",
    "incident_work_orders",
)
TASK_7_PARENT_TABLES = ("incidents",)
TASK_8_TABLES = (
    "task_intents",
    "notification_deliveries",
    "knowledge_chunks",
    "knowledge_ingestion_jobs",
    "knowledge_document_versions",
    "knowledge_documents",
    "user_wechats",
)


def _delete_tables(engine: Engine, tables: tuple[str, ...]) -> None:
    # PA-209: table lists only cover tables the suites know about; rows left in
    # child tables (e.g. user_passwords -> users) made cleanup fail with MySQL
    # 1451 whenever a previous run left data behind. Suspend FK checks for the
    # cleanup window instead of maintaining a second, drifting table order.
    inspector = inspect(engine)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        try:
            for table in tables:
                if inspector.has_table(table):
                    connection.execute(text(f"DELETE FROM `{table}`"))
        finally:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
    )


@pytest.fixture(scope="session")
def test_engine(integration_settings: Settings) -> Iterator[Engine]:
    # PA-118: an unmigrated schema used to surface as one raw MySQL 1146 per test.
    # Check the marker tables once, so a missing schema is a single readable failure.
    verify_test_database_schema(integration_settings)
    assert_disposable_test_database(integration_settings)
    engine = create_database_engine(integration_settings.test_database_url)
    with engine.connect() as connection:
        verify_utc_session(connection)
        version = str(connection.execute(text("SELECT VERSION()")).scalar_one())
        if not version.startswith("8."):
            raise RuntimeError("Task 2 integration tests require MySQL 8")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def clean_property_tables(test_engine: Engine) -> Iterator[None]:
    missing = [
        table for table in TASK_2_TABLES if not inspect(test_engine).has_table(table)
    ]
    if missing:
        raise RuntimeError(
            "Task 2 migration must be upgraded before integration tests: "
            + ", ".join(missing)
        )

    def clean() -> None:
        _delete_tables(
            test_engine,
            TASK_8_TABLES
            + TASK_7_TABLES
            + TASK_6_TABLES
            + TASK_7_PARENT_TABLES
            + TASK_5_TABLES
            + TASK_4_TABLES
            + tuple(reversed(TASK_3_TABLES))
            + tuple(reversed(TASK_2_TABLES)),
        )

    clean()
    try:
        yield
    finally:
        clean()


@pytest.fixture
def db_session(
    integration_settings: Settings,
    clean_property_tables: None,
) -> Iterator[Session]:
    del clean_property_tables
    session = get_session_factory(integration_settings.test_database_url)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clean_identity_tables(
    test_engine: Engine,
    clean_property_tables: None,
) -> Iterator[None]:
    del clean_property_tables
    missing = [
        table for table in TASK_3_TABLES if not inspect(test_engine).has_table(table)
    ]
    if missing:
        raise RuntimeError(
            "Gate A migration must be upgraded before integration tests: "
            + ", ".join(missing)
        )

    def clean() -> None:
        _delete_tables(
            test_engine,
            TASK_8_TABLES
            + TASK_7_TABLES
            + TASK_6_TABLES
            + TASK_7_PARENT_TABLES
            + TASK_5_TABLES
            + TASK_4_TABLES
            + tuple(reversed(TASK_3_TABLES)),
        )

    clean()
    try:
        yield
    finally:
        clean()


@pytest.fixture
def identity_session(
    integration_settings: Settings,
    clean_identity_tables: None,
) -> Iterator[Session]:
    del clean_identity_tables
    session = get_session_factory(integration_settings.test_database_url)()
    try:
        yield session
    finally:
        session.close()


def commit_open_transaction(session: Session) -> None:
    """End SQLAlchemy autobegin from reads so a top-level use case can own begin/commit.

    Auth HTTP uses a separate session. Service tests that load a Principal on the
    business session must close that read transaction first.
    """

    if session.in_transaction():
        session.commit()


def fetch_independent(session: Session, model: type, primary_key: int):
    """Load a row on a different Session/connection. Same-session expire is not persist proof."""

    other = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        return other.get(model, primary_key)
    finally:
        other.close()


def count_independent(session: Session, statement):
    other = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        return other.scalar(statement)
    finally:
        other.close()


@pytest.fixture
def clean_all_tables(test_engine: Engine) -> Iterator[None]:
    """PA-210: wipe every table (except alembic_version) around a test.

    Table-list fixtures drift as new tables appear; suites that assert
    "empty foundation" need a real empty database, not a best-effort list.
    """
    names = [t for t in inspect(test_engine).get_table_names() if t != "alembic_version"]

    def clean() -> None:
        with test_engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                for table in names:
                    connection.execute(text(f"DELETE FROM `{table}`"))
            finally:
                connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    clean()
    try:
        yield
    finally:
        clean()

@pytest.fixture(autouse=True)
def _ensure_schema_head(
    test_engine: Engine,
    integration_settings: Settings,
) -> Iterator[None]:
    """PA-212: migration tests downgrade the shared scratch DB; restore head.

    The database is disposable but shared by every integration test, so a
    downgrade that is not rolled back poisons every suite that runs after it
    with ColumnNotFound-looking 500s. Cheap no-op when already at head.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", integration_settings.test_database_url)

    with test_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    heads = set(ScriptDirectory.from_config(config).get_heads())
    if current not in heads:
        command.upgrade(config, "head")
    yield
    # PA-212: a test that downgraded must not leave the scratch DB behind.
    with test_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current not in heads:
        command.upgrade(config, "head")

