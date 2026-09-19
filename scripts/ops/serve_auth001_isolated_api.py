"""Serve the isolated AUTH-IDENTITY API on 8083. Does not drop the exclusive schema."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import uvicorn
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    assert_isolated_auth_database,
)
from app.main import create_app

PORT = 8083


def main() -> None:
    env_file = next(
        (path for path in (BACKEND / ".env", REPO / ".env") if path.exists()),
        None,
    )
    source = Settings(
        _env_file=str(env_file) if env_file else None,
        app_env="test",
        allow_destructive_test_database=True,
    )
    isolated_url = make_url(source.test_database_url).set(
        database=ISOLATED_AUTH_DATABASE
    ).render_as_string(hide_password=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=isolated_url,
        database_url=source.database_url,
        sms_provider="development",
        dev_auth_enabled=False,
        wechat_login_enabled=True,
        wechat_provider="development",
        api_port=PORT,
        app_secret_key="auth001-isolated-secret-not-production",
        jwt_issuer="test-issuer",
        jwt_audience="test-audience",
    )
    assert_isolated_auth_database(settings, database_url=isolated_url)
    print(
        f"serving={ISOLATED_AUTH_HOST}:{ISOLATED_AUTH_PORT}/{ISOLATED_AUTH_DATABASE} "
        f"api=127.0.0.1:{PORT}/api/v1",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
