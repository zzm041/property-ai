"""Exercise phone-change versus refresh serialization on the isolated AUTH001 DB."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import random
import re
import sys
from threading import Event, Thread
import time

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import get_session_factory  # noqa: E402
from app.core.rate_limit import MemoryRateLimiter  # noqa: E402
from app.domain.identity import normalize_phone  # noqa: E402
from app.provider.sms import development_sms_inbox  # noqa: E402
from app.service.identity.credential_auth import CredentialAuthService  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", prompt)
    if match is None:
        raise RuntimeError("Unsupported visual challenge")
    return str(int(match.group(1)) + int(match.group(2)))


def _service(session, settings) -> CredentialAuthService:
    return CredentialAuthService(
        session,
        settings=settings,
        sms_provider=development_sms_inbox(),
        rate_limiter=MemoryRateLimiter(),
    )


def _send_code(service: CredentialAuthService, phone: str, purpose: str) -> str:
    challenge = service.issue_visual_challenge(
        phone=phone,
        purpose="login",
        request_id="phone-refresh-race-challenge",
    )
    service.send_sms(
        phone=phone,
        purpose=purpose,
        challenge_id=challenge.challenge_id,
        challenge_answer=_answer(challenge.prompt),
        request_id="phone-refresh-race-sms",
    )
    normalized = normalize_phone(phone)
    if normalized is None:
        raise RuntimeError("Generated phone is invalid")
    code = development_sms_inbox().last_code(normalized, purpose)
    if code is None:
        raise RuntimeError("Development SMS code is unavailable")
    return code


def run_probe() -> dict[str, object]:
    settings = build_isolated_settings(enable_enqueue=True)
    session_factory = get_session_factory(settings.test_database_url)
    suffix = f"{random.SystemRandom().randrange(100000000):08d}"
    old_phone = f"137{suffix}"
    new_phone = f"189{suffix}"

    with session_factory() as session:
        service = _service(session, settings)
        first = service.login_sms(
            phone=old_phone,
            code=_send_code(service, old_phone, "login"),
            request_id="phone-refresh-race-login-1",
        )
        second = service.login_sms(
            phone=old_phone,
            code=_send_code(service, old_phone, "login"),
            request_id="phone-refresh-race-login-2",
        )
        old_code = _send_code(service, old_phone, "change_phone")
        new_code = _send_code(service, new_phone, "change_phone")
        user_id = first.user_id

    successor_inserted = Event()
    release_refresh = Event()
    change_reached_user_lock = Event()
    refresh_result: dict[str, object] = {}
    change_result: dict[str, object] = {}

    def refresh_operation() -> None:
        try:
            with session_factory() as session:
                service = _service(session, settings)
                original_issue = service._issue_session

                def paused_issue(*, user_id: int, auth_method: str):
                    issued = original_issue(user_id=user_id, auth_method=auth_method)
                    successor_inserted.set()
                    if not release_refresh.wait(10):
                        raise RuntimeError("Timed out waiting to release refresh")
                    return issued

                service._issue_session = paused_issue  # type: ignore[method-assign]
                issued = service.refresh(
                    refresh_token=first.refresh_token,
                    request_id="phone-refresh-race-refresh",
                )
                refresh_result["session_id"] = issued.session_id
        except Exception as exc:  # pragma: no cover - runtime evidence path
            refresh_result["error"] = f"{type(exc).__name__}: {exc}"
            successor_inserted.set()
            release_refresh.set()

    def change_operation() -> None:
        try:
            with session_factory() as session:
                service = _service(session, settings)
                original_lock = service._lock_active_user

                def observed_lock(target_user_id: int):
                    change_reached_user_lock.set()
                    return original_lock(target_user_id)

                service._lock_active_user = observed_lock  # type: ignore[method-assign]
                issued = service.change_phone(
                    user_id=user_id,
                    old_phone=old_phone,
                    old_code=old_code,
                    new_phone=new_phone,
                    new_code=new_code,
                    request_id="phone-refresh-race-change",
                )
                change_result["session_id"] = issued.session_id
        except Exception as exc:  # pragma: no cover - runtime evidence path
            change_result["error"] = f"{type(exc).__name__}: {exc}"
            change_reached_user_lock.set()
            release_refresh.set()

    refresh_thread = Thread(target=refresh_operation, name="auth001-refresh-race")
    refresh_thread.start()
    if not successor_inserted.wait(10):
        raise RuntimeError("Refresh did not insert its successor")
    change_thread = Thread(target=change_operation, name="auth001-phone-change-race")
    change_thread.start()
    if not change_reached_user_lock.wait(10):
        release_refresh.set()
        raise RuntimeError("Phone change did not reach the user lock")
    time.sleep(0.2)
    change_waited = change_thread.is_alive()
    release_refresh.set()
    refresh_thread.join(10)
    change_thread.join(10)
    if refresh_thread.is_alive() or change_thread.is_alive():
        raise RuntimeError("Concurrency probe threads did not finish")
    if "error" in refresh_result or "error" in change_result:
        raise RuntimeError(
            f"Race operation failed refresh={refresh_result.get('error')} "
            f"change={change_result.get('error')}"
        )

    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        sessions = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, revoked_at FROM auth_sessions "
                    "WHERE user_id = :user_id ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        phone_states = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT status, COUNT(*) AS count FROM user_phones "
                    "WHERE user_id = :user_id GROUP BY status ORDER BY status"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()

    by_id = {item["id"]: item for item in sessions}
    successor_id = int(refresh_result["session_id"])
    replacement_id = int(change_result["session_id"])
    assertions = {
        "phone_change_waited_for_uncommitted_refresh": change_waited,
        "original_refresh_session_revoked": by_id[first.session_id]["revoked_at"] is not None,
        "second_prechange_session_revoked": by_id[second.session_id]["revoked_at"] is not None,
        "refresh_successor_revoked": by_id[successor_id]["revoked_at"] is not None,
        "phone_change_replacement_active": by_id[replacement_id]["revoked_at"] is None,
    }
    return {
        "task_id": "PHONE-CHANGE-001-FIX-1",
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "mode": "real MySQL service concurrency with synthetic data",
        },
        "user_id": user_id,
        "session_ids": {
            "original_refresh": first.session_id,
            "second_prechange": second.session_id,
            "refresh_successor": successor_id,
            "phone_change_replacement": replacement_id,
        },
        "sessions": sessions,
        "phone_status_counts": phone_states,
        "assertions": assertions,
        "status": "PASS" if all(assertions.values()) else "FAIL",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = run_probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["assertions"], ensure_ascii=False, sort_keys=True))
    if evidence["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
