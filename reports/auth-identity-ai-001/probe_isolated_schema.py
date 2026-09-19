"""Print isolated schema host/port/name/revision. Never prints secrets."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    create_database_engine,
)


def main() -> int:
    env_file = next(
        (path for path in (BACKEND / ".env", BACKEND.parent / ".env") if path.exists()),
        None,
    )
    settings = Settings(_env_file=str(env_file) if env_file else None)
    parsed = make_url(settings.test_database_url).set(database=ISOLATED_AUTH_DATABASE)
    print(
        f"target={parsed.host}:{parsed.port or 3306}/{parsed.database}",
        flush=True,
    )
    print(
        f"expected={ISOLATED_AUTH_HOST}:{ISOLATED_AUTH_PORT}/{ISOLATED_AUTH_DATABASE}",
        flush=True,
    )
    engine = create_database_engine(parsed.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            tables = connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name IN "
                    "('visual_challenges', 'password_reset_tickets', 'employee_invites')"
                )
            ).scalar_one()
        print(f"revision={revision}", flush=True)
        print(f"auth001_tables={tables}", flush=True)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
