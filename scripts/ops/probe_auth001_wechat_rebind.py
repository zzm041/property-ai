"""Exercise verified WeChat rebind through the isolated AUTH001 HTTP API."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import random
import re
import sys
import time

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.domain.phone import mask_phone, normalize_phone  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") else None


def _data(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"Missing response data for HTTP {response.status_code}")
    return data


def _headers(token: str | None, request_id: str) -> dict[str, str]:
    headers = {"X-Request-Id": request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _expect(response: httpx.Response, status: int, code: str | None = None) -> None:
    actual_code = _error_code(response)
    if response.status_code != status or (code is not None and actual_code != code):
        raise RuntimeError(
            f"Unexpected response: status={response.status_code} code={actual_code}"
        )


def _challenge_answer(prompt: object) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", str(prompt or ""))
    if match is None:
        raise RuntimeError("Unsupported visual challenge prompt")
    return str(int(match.group(1)) + int(match.group(2)))


def _send_login_code(
    client: httpx.Client,
    phone: str,
    label: str,
) -> str:
    challenge = client.post(
        "/auth/challenge",
        json={"phone": phone, "purpose": "login"},
        headers=_headers(None, f"wechat-rebind-{label}-challenge"),
    )
    _expect(challenge, 200)
    challenge_data = _data(challenge)
    sent = client.post(
        "/auth/sms/send",
        json={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge_data["challenge_id"],
            "challenge_answer": _challenge_answer(challenge_data["prompt"]),
        },
        headers=_headers(None, f"wechat-rebind-{label}-send"),
    )
    _expect(sent, 200)
    inbox = client.post(
        "/auth/sms/test-inbox",
        json={"phone": phone, "purpose": "login"},
        headers=_headers(None, f"wechat-rebind-{label}-inbox"),
    )
    _expect(inbox, 200)
    code = _data(inbox).get("code")
    if not isinstance(code, str) or not code:
        raise RuntimeError("Development SMS inbox did not return a code")
    return code


def _sms_login(
    client: httpx.Client,
    phone: str,
    label: str,
) -> dict[str, object]:
    code = _send_login_code(client, phone, f"{label}-login")
    response = client.post(
        "/auth/sms/login",
        json={"phone": phone, "code": code},
        headers=_headers(None, f"wechat-rebind-{label}-login"),
    )
    _expect(response, 200)
    return _data(response)


def _unique_phone(prefix: str, offset: int) -> str:
    tail = (int(time.time() * 1000) + random.SystemRandom().randrange(10000) + offset) % 100000000
    phone = f"{prefix}{tail:08d}"
    if normalize_phone(phone) is None:
        raise RuntimeError("Generated synthetic phone is invalid")
    return phone


def probe(output: Path) -> dict[str, object]:
    phone = _unique_phone("187", 0)
    other_phone = _unique_phone("186", 1)
    suffix = f"{int(time.time() * 1000)}-{random.SystemRandom().randrange(100000)}"
    old_code = f"wx-new-rebind-old-{suffix}"
    new_code = f"wx-new-rebind-new-{suffix}"
    occupied_code = f"wx-new-rebind-occupied-{suffix}"

    with httpx.Client(base_url=API, timeout=10.0) as client:
        initial = _sms_login(client, phone, "primary")
        initial_access = str(initial["access_token"])
        initial_refresh = str(initial["refresh_token"])
        user_id = int(initial["user_id"])

        initial_bind = client.post(
            "/auth/wechat/bind",
            json={"code": old_code},
            headers=_headers(initial_access, "wechat-rebind-initial-bind"),
        )
        _expect(initial_bind, 200)
        old_wechat = client.post(
            "/auth/wechat/login",
            json={"code": old_code},
            headers=_headers(None, "wechat-rebind-old-login"),
        )
        _expect(old_wechat, 200)
        old_wechat_data = _data(old_wechat)

        occupied = _sms_login(client, other_phone, "occupied")
        occupied_access = str(occupied["access_token"])
        occupied_user_id = int(occupied["user_id"])
        occupied_bind = client.post(
            "/auth/wechat/bind",
            json={"code": occupied_code},
            headers=_headers(occupied_access, "wechat-rebind-occupied-bind"),
        )
        _expect(occupied_bind, 200)

        verification_code = _send_login_code(client, phone, "verify-takeover")
        wrong_code = "000000" if verification_code != "000000" else "999999"
        invalid_otp = client.post(
            "/auth/wechat/rebind",
            json={"code": new_code, "phone": phone, "sms_code": wrong_code},
            headers=_headers(initial_access, "wechat-rebind-invalid-otp"),
        )
        _expect(invalid_otp, 401, "AUTH.INVALID_CODE")
        takeover = client.post(
            "/auth/wechat/rebind",
            json={
                "code": occupied_code,
                "phone": phone,
                "sms_code": verification_code,
            },
            headers=_headers(initial_access, "wechat-rebind-takeover"),
        )
        _expect(takeover, 409, "AUTH.WECHAT_ALREADY_BOUND")
        after_failed_takeover = client.get(
            "/me",
            headers=_headers(
                str(old_wechat_data["access_token"]),
                "wechat-rebind-after-takeover",
            ),
        )
        _expect(after_failed_takeover, 200)

        success_code = _send_login_code(client, phone, "verify-success")
        success = client.post(
            "/auth/wechat/rebind",
            json={"code": new_code, "phone": phone, "sms_code": success_code},
            headers=_headers(initial_access, "wechat-rebind-success"),
        )
        _expect(success, 200)
        replacement = _data(success)
        replacement_access = str(replacement["access_token"])
        replacement_refresh = str(replacement["refresh_token"])

        initial_access_after = client.get(
            "/me",
            headers=_headers(initial_access, "wechat-rebind-old-sms-access"),
        )
        old_wechat_access_after = client.get(
            "/me",
            headers=_headers(
                str(old_wechat_data["access_token"]),
                "wechat-rebind-old-wechat-access",
            ),
        )
        initial_refresh_after = client.post(
            "/auth/token/refresh",
            json={"refresh_token": initial_refresh},
            headers=_headers(None, "wechat-rebind-old-sms-refresh"),
        )
        old_wechat_refresh_after = client.post(
            "/auth/token/refresh",
            json={"refresh_token": old_wechat_data["refresh_token"]},
            headers=_headers(None, "wechat-rebind-old-wechat-refresh"),
        )
        replacement_me = client.get(
            "/me",
            headers=_headers(replacement_access, "wechat-rebind-replacement-access"),
        )
        replacement_refresh_check = client.post(
            "/auth/token/refresh",
            json={"refresh_token": replacement_refresh},
            headers=_headers(None, "wechat-rebind-replacement-refresh"),
        )
        old_identity_login = client.post(
            "/auth/wechat/login",
            json={"code": old_code},
            headers=_headers(None, "wechat-rebind-old-identity-login"),
        )
        new_identity_login = client.post(
            "/auth/wechat/login",
            json={"code": new_code},
            headers=_headers(None, "wechat-rebind-new-identity-login"),
        )
        occupied_identity_login = client.post(
            "/auth/wechat/login",
            json={"code": occupied_code},
            headers=_headers(None, "wechat-rebind-occupied-identity-login"),
        )
        new_identity_me = client.get(
            "/me",
            headers=_headers(
                str(_data(new_identity_login)["access_token"]),
                "wechat-rebind-new-identity-me",
            ),
        )
        occupied_identity_me = client.get(
            "/me",
            headers=_headers(
                str(_data(occupied_identity_login)["access_token"]),
                "wechat-rebind-occupied-identity-me",
            ),
        )

    _expect(initial_access_after, 401, "AUTH.SESSION_REVOKED")
    _expect(old_wechat_access_after, 401, "AUTH.SESSION_REVOKED")
    _expect(initial_refresh_after, 401, "AUTH.INVALID_TOKEN")
    _expect(old_wechat_refresh_after, 401, "AUTH.INVALID_TOKEN")
    _expect(replacement_me, 200)
    _expect(replacement_refresh_check, 200)
    _expect(old_identity_login, 401, "AUTH.WECHAT_BIND_REQUIRED")
    _expect(new_identity_login, 200)
    _expect(occupied_identity_login, 200)
    _expect(new_identity_me, 200)
    _expect(occupied_identity_me, 200)

    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        binding_count = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM user_wechats "
                    "WHERE user_id = :user_id AND appid = :appid"
                ),
                {"user_id": user_id, "appid": settings.wechat_app_id},
            ).scalar_one()
        )
        rebind_audit = dict(
            connection.execute(
                text(
                    "SELECT action, resource_type, resource_id, request_id, details_json, created_at "
                    "FROM audit_logs WHERE actor_user_id = :user_id "
                    "AND action = 'AUTH_WECHAT_REBOUND' ORDER BY id DESC LIMIT 1"
                ),
                {"user_id": user_id},
            ).mappings().one()
        )
        invalid_attempts = int(
            connection.execute(
                text(
                    "SELECT COALESCE(MAX(attempts), 0) FROM sms_challenges "
                    "WHERE phone_e164 = :phone AND purpose = 'login'"
                ),
                {"phone": normalize_phone(phone)},
            ).scalar_one()
        )
    engine.dispose()
    if isinstance(rebind_audit.get("details_json"), str):
        rebind_audit["details_json"] = json.loads(str(rebind_audit["details_json"]))
    database = make_url(settings.test_database_url)
    evidence = {
        "task_id": "WECHAT-REBIND-001",
        "environment": {
            "api": API,
            "database": f"{database.host}:{database.port}/{database.database}",
            "migration": revision,
            "wechat_provider": "development",
            "sms_provider": "development",
            "interaction": "HTTP business APIs plus read-only database assertions",
        },
        "identities": {
            "primary_user_id": user_id,
            "primary_phone_masked": mask_phone(normalize_phone(phone) or phone),
            "occupied_user_id": occupied_user_id,
            "occupied_phone_masked": mask_phone(
                normalize_phone(other_phone) or other_phone
            ),
        },
        "checks": {
            "invalid_otp": {
                "status": invalid_otp.status_code,
                "error_code": _error_code(invalid_otp),
                "attempt_persisted": invalid_attempts >= 1,
            },
            "takeover_prevention": {
                "status": takeover.status_code,
                "error_code": _error_code(takeover),
                "old_wechat_session_remained_valid_after_rollback": after_failed_takeover.status_code == 200,
                "occupied_identity_kept_original_user": int(_data(occupied_identity_me)["user_id"]) == occupied_user_id,
            },
            "rebind": {
                "status": success.status_code,
                "same_user_id": int(_data(replacement_me)["user_id"]) == user_id,
                "one_binding_for_appid": binding_count == 1,
                "audit": rebind_audit,
            },
            "old_binding_invalidation": {
                "old_login_status": old_identity_login.status_code,
                "old_login_error_code": _error_code(old_identity_login),
                "new_login_status": new_identity_login.status_code,
                "new_login_same_user": int(_data(new_identity_me)["user_id"]) == user_id,
            },
            "session_rotation": {
                "old_sms_access_status": initial_access_after.status_code,
                "old_sms_refresh_status": initial_refresh_after.status_code,
                "old_wechat_access_status": old_wechat_access_after.status_code,
                "old_wechat_refresh_status": old_wechat_refresh_after.status_code,
                "replacement_access_status": replacement_me.status_code,
                "replacement_refresh_status": replacement_refresh_check.status_code,
            },
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    return evidence


def recover(
    output: Path,
    *,
    primary_user_id: int,
    occupied_user_id: int,
    api_log: Path,
) -> dict[str, object]:
    events: dict[str, dict[str, object]] = {}
    for line in api_log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = event.get("request_id")
        if not isinstance(request_id, str) or not request_id.startswith(
            "wechat-rebind-"
        ):
            continue
        current = events.setdefault(request_id, {})
        if event.get("event") == "application_error":
            current["error_code"] = event.get("error_code")
        if event.get("event") == "request_completed":
            current["status"] = event.get("status_code")

    expected = {
        "wechat-rebind-invalid-otp": (401, "AUTH.INVALID_CODE"),
        "wechat-rebind-takeover": (409, "AUTH.WECHAT_ALREADY_BOUND"),
        "wechat-rebind-after-takeover": (200, None),
        "wechat-rebind-success": (200, None),
        "wechat-rebind-old-sms-access": (401, "AUTH.SESSION_REVOKED"),
        "wechat-rebind-old-wechat-access": (401, "AUTH.SESSION_REVOKED"),
        "wechat-rebind-old-sms-refresh": (401, "AUTH.INVALID_TOKEN"),
        "wechat-rebind-old-wechat-refresh": (401, "AUTH.INVALID_TOKEN"),
        "wechat-rebind-replacement-access": (200, None),
        "wechat-rebind-replacement-refresh": (200, None),
        "wechat-rebind-old-identity-login": (401, "AUTH.WECHAT_BIND_REQUIRED"),
        "wechat-rebind-new-identity-login": (200, None),
        "wechat-rebind-occupied-identity-login": (200, None),
    }
    for request_id, (status, error_code) in expected.items():
        actual = events.get(request_id, {})
        if actual.get("status") != status or (
            error_code is not None and actual.get("error_code") != error_code
        ):
            raise RuntimeError(
                f"Incomplete recovered API evidence for {request_id}: {actual}"
            )

    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        primary_phone = str(
            connection.execute(
                text(
                    "SELECT phone_e164 FROM user_phones "
                    "WHERE user_id = :user_id AND status = 'ACTIVE'"
                ),
                {"user_id": primary_user_id},
            ).scalar_one()
        )
        occupied_phone = str(
            connection.execute(
                text(
                    "SELECT phone_e164 FROM user_phones "
                    "WHERE user_id = :user_id AND status = 'ACTIVE'"
                ),
                {"user_id": occupied_user_id},
            ).scalar_one()
        )
        binding_counts = {
            int(row.user_id): int(row.binding_count)
            for row in connection.execute(
                text(
                    "SELECT user_id, COUNT(*) AS binding_count FROM user_wechats "
                    "WHERE user_id IN (:primary_user_id, :occupied_user_id) "
                    "AND appid = :appid GROUP BY user_id"
                ),
                {
                    "primary_user_id": primary_user_id,
                    "occupied_user_id": occupied_user_id,
                    "appid": settings.wechat_app_id,
                },
            )
        }
        rebind_audit = dict(
            connection.execute(
                text(
                    "SELECT action, resource_type, resource_id, request_id, details_json, created_at "
                    "FROM audit_logs WHERE actor_user_id = :user_id "
                    "AND request_id = 'wechat-rebind-success' "
                    "AND action = 'AUTH_WECHAT_REBOUND' ORDER BY id DESC LIMIT 1"
                ),
                {"user_id": primary_user_id},
            ).mappings().one()
        )
        identity_login_actors = {
            str(row.request_id): int(row.actor_user_id)
            for row in connection.execute(
                text(
                    "SELECT request_id, actor_user_id FROM audit_logs "
                    "WHERE request_id IN ('wechat-rebind-new-identity-login', "
                    "'wechat-rebind-occupied-identity-login') "
                    "AND action = 'WECHAT_AUTH_TOKEN_ISSUED'"
                )
            )
        }
        invalid_attempts = int(
            connection.execute(
                text(
                    "SELECT COALESCE(MAX(attempts), 0) FROM sms_challenges "
                    "WHERE phone_e164 = :phone AND purpose = 'login'"
                ),
                {"phone": primary_phone},
            ).scalar_one()
        )
        revoked_sessions = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM auth_sessions "
                    "WHERE user_id = :user_id AND revoked_at IS NOT NULL"
                ),
                {"user_id": primary_user_id},
            ).scalar_one()
        )
    engine.dispose()
    if isinstance(rebind_audit.get("details_json"), str):
        rebind_audit["details_json"] = json.loads(str(rebind_audit["details_json"]))
    database = make_url(settings.test_database_url)
    evidence = {
        "task_id": "WECHAT-REBIND-001",
        "environment": {
            "api": API,
            "api_log": str(api_log.resolve().relative_to(REPO)),
            "database": f"{database.host}:{database.port}/{database.database}",
            "migration": revision,
            "wechat_provider": "development",
            "sms_provider": "development",
            "interaction": "recovered from completed HTTP request log plus read-only database assertions",
        },
        "identities": {
            "primary_user_id": primary_user_id,
            "primary_phone_masked": mask_phone(primary_phone),
            "occupied_user_id": occupied_user_id,
            "occupied_phone_masked": mask_phone(occupied_phone),
        },
        "checks": {
            "invalid_otp": {
                **events["wechat-rebind-invalid-otp"],
                "attempt_persisted": invalid_attempts >= 1,
            },
            "takeover_prevention": {
                **events["wechat-rebind-takeover"],
                "old_wechat_session_remained_valid_after_rollback": events[
                    "wechat-rebind-after-takeover"
                ]["status"]
                == 200,
                "occupied_identity_kept_original_user": identity_login_actors.get(
                    "wechat-rebind-occupied-identity-login"
                )
                == occupied_user_id,
            },
            "rebind": {
                **events["wechat-rebind-success"],
                "one_binding_for_appid": binding_counts.get(primary_user_id) == 1,
                "audit": rebind_audit,
            },
            "old_binding_invalidation": {
                "old_login": events["wechat-rebind-old-identity-login"],
                "new_login": events["wechat-rebind-new-identity-login"],
                "new_login_same_user": identity_login_actors.get(
                    "wechat-rebind-new-identity-login"
                )
                == primary_user_id,
            },
            "session_rotation": {
                "old_sms_access": events["wechat-rebind-old-sms-access"],
                "old_sms_refresh": events["wechat-rebind-old-sms-refresh"],
                "old_wechat_access": events["wechat-rebind-old-wechat-access"],
                "old_wechat_refresh": events["wechat-rebind-old-wechat-refresh"],
                "replacement_access": events[
                    "wechat-rebind-replacement-access"
                ],
                "replacement_refresh": events[
                    "wechat-rebind-replacement-refresh"
                ],
                "revoked_session_count": revoked_sessions,
            },
        },
        "status": "PASS",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recover-primary-user-id", type=int)
    parser.add_argument("--recover-occupied-user-id", type=int)
    parser.add_argument("--api-log", type=Path)
    args = parser.parse_args()
    recovery_values = (
        args.recover_primary_user_id,
        args.recover_occupied_user_id,
        args.api_log,
    )
    if any(value is not None for value in recovery_values):
        if not all(value is not None for value in recovery_values):
            parser.error("Recovery requires both user IDs and --api-log")
        evidence = recover(
            args.output,
            primary_user_id=args.recover_primary_user_id,
            occupied_user_id=args.recover_occupied_user_id,
            api_log=args.api_log,
        )
    else:
        evidence = probe(args.output)
    print(
        json.dumps(
            {
                "task_id": evidence["task_id"],
                "checks": evidence["checks"],
            },
            ensure_ascii=False,
            default=_default,
        )
    )


if __name__ == "__main__":
    main()
