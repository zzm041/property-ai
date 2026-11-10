"""Guarded disposable MySQL fixtures for Task 2 integration tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    assert_disposable_test_database,
    create_database_engine,
    get_session_factory,
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
    "work_order_sla_applications",
    "work_order_feedbacks",
    "work_order_receipts",
    "work_order_processes",
    "work_order_logs",
    "work_order_media",
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
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in tables:
            if inspector.has_table(table):
                connection.execute(text(f"DELETE FROM `{table}`"))


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
    )


@pytest.fixture(scope="session")
def test_engine(integration_settings: Settings) -> Iterator[Engine]:
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
