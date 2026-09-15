"""Synchronous SQLAlchemy engine, sessions, UTC setup, and test safety."""

from collections.abc import Generator
from functools import lru_cache
from threading import Lock
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

_engines: set[Engine] = set()
_engines_lock = Lock()


def _database_target(url: str) -> tuple[str | None, int, str | None]:
    parsed = make_url(url)
    return parsed.host, parsed.port or 3306, parsed.database


def _require_mysql_pymysql(url: URL) -> None:
    if url.drivername != "mysql+pymysql":
        raise ValueError("Database engine must use mysql+pymysql")


def _set_utc_session(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


def create_database_engine(
    database_url: str,
    *,
    connect_timeout: int = 5,
) -> Engine:
    """Create one sync PyMySQL engine whose every connection is explicitly UTC."""

    parsed = make_url(database_url)
    _require_mysql_pymysql(parsed)
    engine = create_engine(
        parsed,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
        connect_args={
            "connect_timeout": connect_timeout,
            "read_timeout": connect_timeout,
            "write_timeout": connect_timeout,
        },
    )
    event.listen(engine, "connect", _set_utc_session)
    with _engines_lock:
        _engines.add(engine)
    return engine


@lru_cache(maxsize=8)
def get_engine(database_url: str, connect_timeout: int = 5) -> Engine:
    """Return a process-local engine for a validated database URL."""

    return create_database_engine(database_url, connect_timeout=connect_timeout)


@lru_cache(maxsize=4)
def get_session_factory(database_url: str) -> sessionmaker[Session]:
    """Return the sync Session factory for an environment-specific engine."""

    return sessionmaker(
        bind=get_engine(database_url),
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency: create, yield, and close; never commit business work."""

    settings = get_settings()
    session = get_session_factory(settings.active_database_url)()
    try:
        yield session
    finally:
        session.close()


def verify_utc_session(connection: Connection) -> None:
    """Fail fast if an application or Alembic connection is not explicitly UTC."""

    timezone = connection.execute(text("SELECT @@session.time_zone")).scalar_one()
    if timezone != "+00:00":
        raise RuntimeError("MySQL session timezone must be +00:00")


def assert_disposable_test_database(
    settings: Settings,
    *,
    database_url: str | None = None,
) -> None:
    """Authorize destructive migration tests only for an explicit disposable DB."""

    candidate = database_url or settings.active_database_url
    parsed = make_url(candidate)
    _require_mysql_pymysql(parsed)

    if settings.app_env != "test":
        raise RuntimeError("Destructive database operation requires APP_ENV=test")
    if not settings.allow_destructive_test_database:
        raise RuntimeError(
            "Destructive database operation requires "
            "ALLOW_DESTRUCTIVE_TEST_DATABASE=true"
        )
    if _database_target(candidate) != _database_target(settings.test_database_url):
        raise RuntimeError("Destructive operation must target TEST_DATABASE_URL")
    if _database_target(candidate) == _database_target(settings.database_url):
        raise RuntimeError("Test database must differ from the development database")
    if not parsed.database or "test" not in parsed.database.casefold():
        raise RuntimeError("Disposable database name must contain 'test'")


def dispose_database_engines() -> None:
    """Dispose all cached pools, primarily during process shutdown and tests."""

    with _engines_lock:
        engines = tuple(_engines)
        _engines.clear()
    for engine in engines:
        engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
