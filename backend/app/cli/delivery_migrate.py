"""Guarded clean-delivery migration through the fixed 0015 revision."""
from __future__ import annotations

import os
import subprocess

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.cli.bootstrap_system_admin import main as bootstrap_system_admin
from app.core.config import get_settings


HEAD = "20261003_0015_work_order_media_cycle_links"
IDENTITY_SLA_REVISION = "20260926_0014_identity_merge_sla_versions"
PRE_BOOTSTRAP = "20260925_0012_task_intents"
LIVE_DATABASE = "property_ai_delivery_check"
DRILL_DATABASE = "property_ai_delivery_media_cycle_check"


def _target() -> tuple[str, str]:
    settings = get_settings()
    url = make_url(settings.active_database_url)
    actual = f"{url.host}:{url.port}/{url.database}"
    allowed_database = (
        url.database == LIVE_DATABASE
        and os.environ.get("PROPERTY_AI_MEDIA_CYCLE_MIGRATION") == "approved"
    ) or (
        url.database == DRILL_DATABASE
        and os.environ.get("PROPERTY_AI_MEDIA_CYCLE_DRILL") == "approved"
    )
    failures = [
        name
        for name, valid in (
            (
                "MASTER_APPROVAL",
                os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") == "approved",
            ),
            (
                "TARGET_APPROVAL",
                os.environ.get("PROPERTY_AI_APPROVE_DELIVERY_TARGET") == actual,
            ),
            ("APP_ENV", settings.app_env == "staging"),
            ("DRIVER", url.drivername == "mysql+pymysql"),
            ("HOST", url.host == "mysql"),
            ("PORT", url.port == 3306),
            ("DATABASE_APPROVAL", allowed_database),
        )
        if not valid
    ]
    if failures:
        raise RuntimeError(
            "delivery migration refused outside the exact configured target: "
            + ",".join(failures)
        )
    return url.render_as_string(hide_password=False), actual


def _revision(engine) -> str | None:
    with engine.connect() as connection:
        exists = connection.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='alembic_version'"
        )).scalar_one()
        if not exists:
            return None
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _alembic(target: str) -> None:
    subprocess.run(["alembic", "upgrade", target], check=True)


def main() -> int:
    database_url, actual = _target()
    url = make_url(database_url)
    os.environ["DATABASE_URL"] = database_url
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        current = _revision(engine)
        if current == HEAD:
            print(f"delivery database already at {HEAD}")
            return 0
        if current not in {None, PRE_BOOTSTRAP, IDENTITY_SLA_REVISION}:
            raise RuntimeError(f"delivery migration refuses unexpected revision {current!r}")
        if current is None:
            _alembic(PRE_BOOTSTRAP)
        if current in {None, PRE_BOOTSTRAP}:
            bootstrap_system_admin()
            with engine.begin() as connection:
                column = connection.execute(text(
                    "SELECT CHARACTER_MAXIMUM_LENGTH,IS_NULLABLE,COLUMN_KEY FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name='alembic_version' AND column_name='version_num'"
                )).one()
                if tuple(column) == (32, "NO", "PRI"):
                    connection.execute(text(
                        "ALTER TABLE alembic_version MODIFY version_num VARCHAR(64) NOT NULL"
                    ))
                elif tuple(column) != (64, "NO", "PRI"):
                    raise RuntimeError(f"unexpected alembic version column shape: {tuple(column)!r}")
            os.environ["PROPERTY_AI_APPROVE_0014_DATABASE"] = url.database
            os.environ["PROPERTY_AI_APPROVE_0014_TARGET"] = actual
            _alembic(IDENTITY_SLA_REVISION)
        os.environ["PROPERTY_AI_APPROVE_0015_DATABASE"] = url.database
        os.environ["PROPERTY_AI_APPROVE_0015_TARGET"] = actual
        _alembic(HEAD)
        if _revision(engine) != HEAD:
            raise RuntimeError("delivery migration did not reach the expected head")
        print(f"delivery database migrated to {HEAD}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
