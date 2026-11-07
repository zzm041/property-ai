"""Official credential runtime against the exclusive AUTH-IDENTITY 0009 schema.

Requires AUTH_ISOLATED_DATABASE_URL. Absence is skip, not PASS.
When the URL is set, missing 0009 tables fail the run.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database import (
    ISOLATED_AUTH_DATABASE,
    ISOLATED_AUTH_HOST,
    ISOLATED_AUTH_PORT,
    assert_isolated_auth_database,
    create_database_engine,
)
from app.core.token_hash import keyed_hash
from app.main import create_app

ISOLATED_URL = os.environ.get("AUTH_ISOLATED_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not ISOLATED_URL,
    reason=(
        "AUTH_ISOLATED_DATABASE_URL not provisioned; "
        "isolated 0009 runtime is not claimed as PASS"
    ),
)

REQUIRED_TABLES = (
    "user_phones",
    "user_passwords",
    "sms_challenges",
    "visual_challenges",
    "password_reset_tickets",
    "auth_sessions",
    "employee_invites",
)
SECRET = "auth001-isolated-secret-not-production"


def _settings() -> Settings:
    parsed = make_url(ISOLATED_URL)
    assert parsed.host == ISOLATED_AUTH_HOST
    assert (parsed.port or 3306) == ISOLATED_AUTH_PORT
    assert parsed.database == ISOLATED_AUTH_DATABASE
    settings = Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=ISOLATED_URL,
        database_url=(
            "mysql+pymysql://property:change-me@127.0.0.1:3306/"
            "property_ai?charset=utf8mb4"
        ),
        sms_provider="development",
        dev_auth_enabled=False,
        wechat_login_enabled=True,
        wechat_provider="development",
        app_secret_key=SECRET,
        jwt_issuer="test-issuer",
        jwt_audience="test-audience",
    )
    assert_isolated_auth_database(settings, database_url=ISOLATED_URL)
    return settings


@pytest.fixture(scope="module")
def isolated_settings() -> Settings:
    return _settings()


@pytest.fixture(scope="module")
def isolated_client(isolated_settings: Settings):
    with TestClient(create_app(isolated_settings)) as client:
        yield client


def _phone() -> str:
    return f"139{secrets.randbelow(100_000_000):08d}"


def _answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+) \+ (\d+) = \?", prompt.strip())
    assert match is not None
    return str(int(match.group(1)) + int(match.group(2)))


def _error(response) -> str:
    payload = response.json()
    return str(payload.get("error", {}).get("code") or "")


def _issue(client: TestClient, phone: str, purpose: str = "login") -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/challenge",
        json={"phone": phone, "purpose": purpose},
        headers={"X-Request-Id": f"ch-{phone[-4:]}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data["challenge_id"], _answer(data["prompt"])


def _inbox(client: TestClient, phone: str, purpose: str) -> str:
    response = client.post(
        "/api/v1/auth/sms/test-inbox",
        json={"phone": phone, "purpose": purpose},
        headers={"X-Request-Id": f"inbox-{purpose}"},
    )
    assert response.status_code == 200, response.text
    code = response.json()["data"]["code"]
    assert code
    return code


def _send(
    client: TestClient,
    phone: str,
    purpose: str,
    challenge_id: str,
    answer: str,
) -> object:
    return client.post(
        "/api/v1/auth/sms/send",
        json={
            "phone": phone,
            "purpose": purpose,
            "challenge_id": challenge_id,
            "challenge_answer": answer,
        },
        headers={"X-Request-Id": f"sms-{purpose}"},
    )


def test_isolated_schema_keeps_0007_user_and_0009_constraints(
    isolated_settings: Settings,
) -> None:
    engine = create_database_engine(ISOLATED_URL)
    try:
        inspector = inspect(engine)
        missing = [name for name in REQUIRED_TABLES if not inspector.has_table(name)]
        assert missing == []
        phones = {item["name"] for item in inspector.get_unique_constraints("user_phones")} | {
            item["name"] for item in inspector.get_indexes("user_phones")
        }
        assert "uq_user_phones_active_e164" in phones
        passwords = {
            item["name"] for item in inspector.get_unique_constraints("user_passwords")
        } | {item["name"] for item in inspector.get_indexes("user_passwords")}
        assert "uq_user_passwords_user_id" in passwords
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            baseline = connection.execute(
                text(
                    "SELECT id, display_name FROM users "
                    "WHERE display_name = 'auth001-0007-baseline'"
                )
            ).one()
        assert revision == "20260919_0009"
        assert baseline.display_name == "auth001-0007-baseline"
        users_columns = {column["name"] for column in inspector.get_columns("users")}
        assert "profile_completed_at" in users_columns
    finally:
        engine.dispose()


def test_wrong_challenge_does_not_send_sms(isolated_client: TestClient) -> None:
    phone = _phone()
    challenge_id, _ = _issue(isolated_client, phone)
    rejected = _send(isolated_client, phone, "login", challenge_id, "99")
    assert rejected.status_code == 401
    assert _error(rejected) == "AUTH.CHALLENGE_INVALID"
    inbox = isolated_client.post(
        "/api/v1/auth/sms/test-inbox",
        json={"phone": phone, "purpose": "login"},
        headers={"X-Request-Id": "inbox-miss"},
    )
    assert inbox.status_code == 200
    assert inbox.json()["data"]["code"] is None
    extra = isolated_client.post(
        "/api/v1/auth/sms/send",
        json={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge_id,
            "challenge_answer": "1",
            "captcha_passed": True,
        },
        headers={"X-Request-Id": "captcha-bool"},
    )
    assert extra.status_code == 422


def test_expired_challenge_does_not_send_sms(
    isolated_client: TestClient,
    isolated_settings: Settings,
) -> None:
    phone = _phone()
    challenge_id, answer = _issue(isolated_client, phone)
    token_hash = keyed_hash(
        challenge_id, secret=isolated_settings.app_secret_key.get_secret_value()
    )
    engine = create_database_engine(ISOLATED_URL)
    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE visual_challenges SET expires_at = :expired "
                    "WHERE token_hash = :token_hash"
                ),
                {
                    "expired": datetime(2000, 1, 1),
                    "token_hash": token_hash,
                },
            )
            assert updated.rowcount == 1
    finally:
        engine.dispose()
    rejected = _send(isolated_client, phone, "login", challenge_id, answer)
    assert rejected.status_code == 401
    assert _error(rejected) == "AUTH.CHALLENGE_INVALID"


def run_full_sms_auth_flow(
    isolated_client: TestClient,
) -> tuple[str, dict]:
    phone = _phone()
    challenge_id, answer = _issue(isolated_client, phone, "login")
    sent = _send(isolated_client, phone, "login", challenge_id, answer)
    assert sent.status_code == 200, sent.text
    data = sent.json()["data"]
    assert data["delivery_channel"] == "development"
    assert "code" not in data
    code = _inbox(isolated_client, phone, "login")
    login = isolated_client.post(
        "/api/v1/auth/sms/login",
        json={"phone": phone, "code": code},
        headers={"X-Request-Id": "sms-login"},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()["data"]
    assert tokens["auth_method"] == "sms"
    assert tokens["user_id"] > 0
    me = isolated_client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "X-Request-Id": "me-sms",
        },
    )
    assert me.status_code == 200
    assert me.json()["data"]["roles"] == []
    replay = isolated_client.post(
        "/api/v1/auth/sms/login",
        json={"phone": phone, "code": code},
        headers={"X-Request-Id": "sms-replay"},
    )
    assert replay.status_code == 401
    assert _error(replay) == "AUTH.INVALID_CODE"
    wrong = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": phone, "password": "GoodPass1", "role": "PROPERTY_MANAGER"},
        headers={"X-Request-Id": "role-select"},
    )
    assert wrong.status_code == 422

    reset_id, reset_answer = _issue(isolated_client, phone, "reset")
    reset_send = _send(isolated_client, phone, "reset", reset_id, reset_answer)
    assert reset_send.status_code == 200, reset_send.text
    reset_code = _inbox(isolated_client, phone, "reset")
    verify = isolated_client.post(
        "/api/v1/auth/password/reset/verify",
        json={"phone": phone, "code": reset_code},
        headers={"X-Request-Id": "reset-verify"},
    )
    assert verify.status_code == 200, verify.text
    reset_token = verify.json()["data"]["reset_token"]
    assert verify.json()["data"].get("access_token") is None
    mismatch = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": reset_token,
            "new_password": "GoodPass1",
            "confirm_password": "GoodPass2",
        },
        headers={"X-Request-Id": "reset-mismatch"},
    )
    assert mismatch.status_code == 422
    assert _error(mismatch) == "AUTH.PASSWORD_MISMATCH"
    weak = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": reset_token,
            "new_password": "12345678",
            "confirm_password": "12345678",
        },
        headers={"X-Request-Id": "reset-weak"},
    )
    assert weak.status_code == 422
    assert _error(weak) == "AUTH.WEAK_PASSWORD"
    confirm = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": reset_token,
            "new_password": "GoodPass1",
            "confirm_password": "GoodPass1",
        },
        headers={"X-Request-Id": "reset-ok"},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["data"]["ok"] is True
    assert "access_token" not in confirm.json()["data"]
    replay_ticket = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": reset_token,
            "new_password": "GoodPass1",
            "confirm_password": "GoodPass1",
        },
        headers={"X-Request-Id": "reset-replay"},
    )
    assert replay_ticket.status_code == 401
    old_access = isolated_client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "X-Request-Id": "me-revoked",
        },
    )
    assert old_access.status_code == 401
    old_refresh = isolated_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"X-Request-Id": "refresh-revoked"},
    )
    assert old_refresh.status_code == 401
    password_login = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": phone, "password": "GoodPass1"},
        headers={"X-Request-Id": "password-ok"},
    )
    assert password_login.status_code == 200, password_login.text
    assert password_login.json()["data"]["auth_method"] == "password"
    stale = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": phone, "password": "OldPass99"},
        headers={"X-Request-Id": "password-old"},
    )
    assert stale.status_code == 401
    assert _error(stale) == "AUTH.INVALID_PASSWORD"

    # This is a separate forgotten-password action after the first password
    # has been used, not a re-labeling of the initial-password setup.
    active = password_login.json()["data"]
    assert active["user_id"] == tokens["user_id"]
    forgot_id, forgot_answer = _issue(isolated_client, phone, "reset")
    forgot_send = _send(isolated_client, phone, "reset", forgot_id, forgot_answer)
    assert forgot_send.status_code == 200, forgot_send.text
    forgot_code = _inbox(isolated_client, phone, "reset")
    forgot_verify = isolated_client.post(
        "/api/v1/auth/password/reset/verify",
        json={"phone": phone, "code": forgot_code},
        headers={"X-Request-Id": "forgot-verify"},
    )
    assert forgot_verify.status_code == 200, forgot_verify.text
    forgot_ticket = forgot_verify.json()["data"]["reset_token"]
    forgot_confirm = isolated_client.post(
        "/api/v1/auth/password/reset/confirm",
        json={
            "reset_token": forgot_ticket,
            "new_password": "NewPass2Strong",
            "confirm_password": "NewPass2Strong",
        },
        headers={"X-Request-Id": "forgot-confirm"},
    )
    assert forgot_confirm.status_code == 200, forgot_confirm.text
    old_password = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": phone, "password": "GoodPass1"},
        headers={"X-Request-Id": "forgot-old-password"},
    )
    assert old_password.status_code == 401
    assert _error(old_password) == "AUTH.INVALID_PASSWORD"
    old_session = isolated_client.get(
        "/api/v1/me",
        headers={
            "Authorization": f"Bearer {active['access_token']}",
            "X-Request-Id": "forgot-old-access",
        },
    )
    assert old_session.status_code == 401
    old_refresh_after_forgot = isolated_client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": active["refresh_token"]},
        headers={"X-Request-Id": "forgot-old-refresh"},
    )
    assert old_refresh_after_forgot.status_code == 401
    new_login = isolated_client.post(
        "/api/v1/auth/password/login",
        json={"phone": phone, "password": "NewPass2Strong"},
        headers={"X-Request-Id": "forgot-new-password"},
    )
    assert new_login.status_code == 200, new_login.text
    assert new_login.json()["data"]["user_id"] == tokens["user_id"]
    return phone, new_login.json()["data"]


def test_sms_login_otp_reuse_and_password_reset_session_revoke(
    isolated_client: TestClient,
) -> None:
    run_full_sms_auth_flow(isolated_client)


def test_expired_otp_is_rejected(
    isolated_client: TestClient,
    isolated_settings: Settings,
) -> None:
    phone = _phone()
    challenge_id, answer = _issue(isolated_client, phone)
    sent = _send(isolated_client, phone, "login", challenge_id, answer)
    assert sent.status_code == 200
    code = _inbox(isolated_client, phone, "login")
    engine = create_database_engine(ISOLATED_URL)
    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE sms_challenges SET expires_at = :expired "
                    "WHERE phone_e164 = :phone AND purpose = 'login' "
                    "AND consumed_at IS NULL"
                ),
                {
                    "expired": datetime.now(UTC) - timedelta(minutes=10),
                    "phone": f"+86{phone}",
                },
            )
            assert updated.rowcount == 1
    finally:
        engine.dispose()
    login = isolated_client.post(
        "/api/v1/auth/sms/login",
        json={"phone": phone, "code": code},
        headers={"X-Request-Id": "otp-expired"},
    )
    assert login.status_code == 401
    assert _error(login) == "AUTH.INVALID_CODE"
    del isolated_settings


def test_phone_change_cannot_reuse_old_challenge(isolated_client: TestClient) -> None:
    first = _phone()
    second = _phone()
    challenge_id, answer = _issue(isolated_client, first)
    rejected = _send(isolated_client, second, "login", challenge_id, answer)
    assert rejected.status_code == 401
    assert _error(rejected) == "AUTH.CHALLENGE_INVALID"
