"""Provision exclusive property_ai_auth001_test and run isolated 0009 auth tests.

Does not modify property_ai, property_ai_test, property_ai_day4, or shared containers.
Does not print passwords, OTP codes, or full database URLs.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    assert_isolated_auth_database,
    create_database_engine,
    dispose_database_engines,
)

BASELINE_NAME = "auth001-0007-baseline"


def _isolated_url(settings: Settings) -> str:
    parsed = make_url(settings.test_database_url)
    isolated = parsed.set(database=ISOLATED_AUTH_DATABASE)
    return isolated.render_as_string(hide_password=False)


def _print_target(url: str) -> None:
    parsed = make_url(url)
    print(
        f"isolated_target={parsed.host}:{parsed.port or 3306}/{parsed.database}",
        flush=True,
    )


def _mysql_test_container() -> str:
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=mysql-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or not names:
        raise RuntimeError(
            "mysql-test container is not running; this script will not recreate it"
        )
    return names[0]


def _create_isolated_schema(username: str) -> None:
    container = _mysql_test_container()
    sql = (
        f"DROP DATABASE IF EXISTS `{ISOLATED_AUTH_DATABASE}`; "
        f"CREATE DATABASE `{ISOLATED_AUTH_DATABASE}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; "
        f"GRANT ALL PRIVILEGES ON `{ISOLATED_AUTH_DATABASE}`.* "
        f"TO '{username}'@'%'; FLUSH PRIVILEGES;"
    )
    completed = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            "mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" -e " + shlex.quote(sql),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "could not create isolated schema via mysql-test root; "
            f"container={container} returncode={completed.returncode}"
        )


def main() -> int:
    os.chdir(BACKEND)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    source_candidates = [BACKEND / ".env", REPO / ".env"]
    env_file = next((path for path in source_candidates if path.exists()), None)
    source_settings = Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )
    isolated_url = _isolated_url(source_settings)
    _print_target(isolated_url)
    isolated_settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=isolated_url,
        database_url=source_settings.database_url,
    )
    assert_isolated_auth_database(isolated_settings, database_url=isolated_url)
    username = make_url(isolated_url).username or "property_test"
    _create_isolated_schema(username)

    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = isolated_url
    command.upgrade(config, "20260917_0007")

    engine = create_database_engine(isolated_url)
    try:
        with engine.begin() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != "20260917_0007":
                raise RuntimeError(f"expected 0007 baseline, got {revision}")
            connection.execute(
                text(
                    "INSERT INTO users (status, display_name) "
                    "VALUES ('ACTIVE', :name)"
                ),
                {"name": BASELINE_NAME},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "20260919_0009")
    engine = create_database_engine(isolated_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            kept = connection.execute(
                text(
                    "SELECT display_name FROM users WHERE display_name = :name"
                ),
                {"name": BASELINE_NAME},
            ).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(text("SHOW TABLES")).all()
            }
        if revision != "20260919_0009":
            raise RuntimeError(f"expected 0009 head, got {revision}")
        if kept != BASELINE_NAME:
            raise RuntimeError("0007 synthetic user was not preserved")
        for name in (
            "user_phones",
            "user_passwords",
            "sms_challenges",
            "visual_challenges",
            "password_reset_tickets",
            "auth_sessions",
        ):
            if name not in tables:
                raise RuntimeError(f"missing {name} after 0009")
    finally:
        engine.dispose()

    print(
        f"migrated={ISOLATED_AUTH_HOST}:{ISOLATED_AUTH_PORT}/{ISOLATED_AUTH_DATABASE}"
        " revisions=20260917_0007->20260919_0009",
        flush=True,
    )
    env = os.environ.copy()
    env["AUTH_ISOLATED_DATABASE_URL"] = isolated_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_isolated_auth_runtime.py",
            "-q",
            "--tb=short",
        ],
        cwd=str(BACKEND),
        env=env,
        check=False,
    )
    dispose_database_engines()
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
