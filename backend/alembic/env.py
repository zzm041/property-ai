"""Alembic environment using the same sync PyMySQL/UTC setup as the API."""

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import Connection

from app.core.config import Settings
from app.core.database import create_database_engine, verify_utc_session
from app.core.time import UTCDateTime
from app.model import Base  # Imports all Task 2 models into metadata.

config = context.config
settings = Settings()
database_url = config.attributes.get("database_url", settings.active_database_url)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def render_item(
    object_type: str,
    object_: object,
    autogen_context: AutogenContext,
) -> str | bool:
    """Keep historical migrations independent from the application type class."""

    if object_type == "type" and isinstance(object_, UTCDateTime):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "mysql.DATETIME(fsp=6)"
    return False


def configure_context(connection: Connection) -> None:
    verify_utc_session(connection)
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        configure_context(supplied_connection)
        return

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            configure_context(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
