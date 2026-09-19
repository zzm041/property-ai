"""Exercise reverse refresh and logout lock order against AUTH001 MySQL."""

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
from app.core.errors import AppError, AuthErrorCode  # noqa: E402
from app.core.rate_limit import MemoryRateLimiter  # noqa: E402
from app.domain.phone import normalize_phone  # noqa: E402
from app.provider.sms import development_sms_inbox  # noqa: E402
from app.service.credential_auth import CredentialAuthService  # noqa: E402
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
        request_id="phone-reverse-race-challenge",
    )
    service.send_sms(
        phone=phone,
        purpose=purpose,
        challenge_id=challenge.challenge_id,
        challenge_answer=_answer(challenge.prompt),
        request_id="phone-reverse-race-sms",
    )
    normalized = normalize_phone(phone)
    if normalized is None:
        raise RuntimeError("Generated phone is invalid")
    code = development_sms_inbox().last_code(normalized, purpose)
    if code is None:
        raise RuntimeError("Development SMS code is unavailable")
    return code


def _prepare(session_factory, settings, *, prefix: str) -> dict[str, object]:
    suffix = f"{random.SystemRandom().randrange(100000000):08d}"
    old_phone = f"{prefix}{suffix}"
    new_phone = f"189{suffix}"
    with session_factory() as session:
        service = _service(session, settings)
        first = service.login_sms(
            phone=old_phone,
            code=_send_code(service, old_phone, "login"),
            request_id="phone-reverse-race-login-1",
        )
        second = service.login_sms(
            phone=old_phone,
            code=_send_code(service, old_phone, "login"),
            request_id="phone-reverse-race-login-2",
        )
        old_code = _send_code(service, old_phone, "change_phone")
        new_code = _send_code(service, new_phone, "change_phone")
    return {
        "user_id": first.user_id,
        "old_phone": old_phone,
        "new_phone": new_phone,
        "old_code": old_code,
        "new_code": new_code,
        "first": first,
        "second": second,
    }


def _sessions(settings, user_id: int) -> list[dict[str, object]]:
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, revoked_at FROM auth_sessions "
                    "WHERE user_id = :user_id ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
    engine.dispose()
    return rows


def _change_holding_user(
    prepared: dict[str, object],
    session_factory,
    settings,
    holds_user: Event,
    release_change: Event,
    result: dict[str, object],
) -> None:
    try:
        with session_factory() as session:
            service = _service(session, settings)
            original_lock = service._lock_active_user
            paused = False

            def paused_lock(user_id: int):
                nonlocal paused
                user = original_lock(user_id)
                if not paused:
                    paused = True
                    holds_user.set()
                    if not release_change.wait(10):
                        raise RuntimeError("Timed out waiting to release phone change")
                return user

            service._lock_active_user = paused_lock  # type: ignore[method-assign]
            issued = service.change_phone(
                user_id=int(prepared["user_id"]),
                old_phone=str(prepared["old_phone"]),
                old_code=str(prepared["old_code"]),
                new_phone=str(prepared["new_phone"]),
                new_code=str(prepared["new_code"]),
                request_id="phone-reverse-race-change",
            )
            result["session_id"] = issued.session_id
    except Exception as exc:  # pragma: no cover - runtime evidence path
        result["error"] = f"{type(exc).__name__}: {exc}"
        holds_user.set()
        release_change.set()


def _run_reverse_refresh(session_factory, settings) -> dict[str, object]:
    prepared = _prepare(session_factory, settings, prefix="136")
    holds_user = Event()
    release_change = Event()
    discovered_session = Event()
    change_result: dict[str, object] = {}
    refresh_result: dict[str, object] = {}

    change_thread = Thread(
        target=_change_holding_user,
        args=(
            prepared,
            session_factory,
            settings,
            holds_user,
            release_change,
            change_result,
        ),
        name="auth001-reverse-change",
    )
    change_thread.start()
    if not holds_user.wait(10):
        raise RuntimeError("Phone change did not acquire the user lock")

    def refresh_operation() -> None:
        try:
            with session_factory() as session:
                service = _service(session, settings)
                original_discovery = (
                    service._repository.get_session_by_refresh_hash_unlocked
                )

                def observed_discovery(token_hash: str):
                    candidate = original_discovery(token_hash)
                    discovered_session.set()
                    return candidate

                service._repository.get_session_by_refresh_hash_unlocked = (
                    observed_discovery
                )
                issued = service.refresh(
                    refresh_token=prepared["first"].refresh_token,
                    request_id="phone-reverse-race-refresh",
                )
                refresh_result["session_id"] = issued.session_id
        except AppError as exc:
            refresh_result["error_code"] = exc.code
            discovered_session.set()
        except Exception as exc:  # pragma: no cover - runtime evidence path
            refresh_result["error"] = f"{type(exc).__name__}: {exc}"
            discovered_session.set()

    refresh_thread = Thread(target=refresh_operation, name="auth001-reverse-refresh")
    refresh_thread.start()
    if not discovered_session.wait(10):
        release_change.set()
        raise RuntimeError("Refresh did not discover the pre-change session")
    time.sleep(0.2)
    refresh_waited = refresh_thread.is_alive()
    release_change.set()
    change_thread.join(10)
    refresh_thread.join(10)
    if change_thread.is_alive() or refresh_thread.is_alive():
        raise RuntimeError("Reverse refresh threads did not finish")
    if "error" in change_result or "error" in refresh_result:
        raise RuntimeError(
            f"Reverse refresh failed change={change_result.get('error')} "
            f"refresh={refresh_result.get('error')}"
        )

    sessions = _sessions(settings, int(prepared["user_id"]))
    by_id = {item["id"]: item for item in sessions}
    replacement_id = int(change_result["session_id"])
    assertions = {
        "refresh_discovered_session_then_waited_for_user": refresh_waited,
        "refresh_rejected_after_phone_change": refresh_result.get("error_code")
        == AuthErrorCode.INVALID_TOKEN,
        "no_refresh_successor_created": len(sessions) == 3,
        "both_prechange_sessions_revoked": (
            by_id[prepared["first"].session_id]["revoked_at"] is not None
            and by_id[prepared["second"].session_id]["revoked_at"] is not None
        ),
        "phone_change_replacement_active": by_id[replacement_id]["revoked_at"] is None,
    }
    return {
        "user_id": prepared["user_id"],
        "sessions": sessions,
        "refresh_result": refresh_result,
        "assertions": assertions,
    }


def _run_logout_race(session_factory, settings) -> dict[str, object]:
    prepared = _prepare(session_factory, settings, prefix="135")
    holds_user = Event()
    release_change = Event()
    discovered_session = Event()
    change_result: dict[str, object] = {}
    logout_result: dict[str, object] = {}

    change_thread = Thread(
        target=_change_holding_user,
        args=(
            prepared,
            session_factory,
            settings,
            holds_user,
            release_change,
            change_result,
        ),
        name="auth001-logout-change",
    )
    change_thread.start()
    if not holds_user.wait(10):
        raise RuntimeError("Phone change did not acquire the user lock")

    def logout_operation() -> None:
        try:
            with session_factory() as session:
                service = _service(session, settings)
                original_discovery = (
                    service._repository.get_session_by_refresh_hash_unlocked
                )

                def observed_discovery(token_hash: str):
                    candidate = original_discovery(token_hash)
                    discovered_session.set()
                    return candidate

                service._repository.get_session_by_refresh_hash_unlocked = (
                    observed_discovery
                )
                service.logout(
                    session_id=None,
                    refresh_token=prepared["first"].refresh_token,
                    request_id="phone-logout-race",
                )
                logout_result["completed"] = True
        except Exception as exc:  # pragma: no cover - runtime evidence path
            logout_result["error"] = f"{type(exc).__name__}: {exc}"
            discovered_session.set()

    logout_thread = Thread(target=logout_operation, name="auth001-concurrent-logout")
    logout_thread.start()
    if not discovered_session.wait(10):
        release_change.set()
        raise RuntimeError("Logout did not discover the pre-change session")
    time.sleep(0.2)
    logout_waited = logout_thread.is_alive()
    release_change.set()
    change_thread.join(10)
    logout_thread.join(10)
    if change_thread.is_alive() or logout_thread.is_alive():
        raise RuntimeError("Logout race threads did not finish")
    if "error" in change_result or "error" in logout_result:
        raise RuntimeError(
            f"Logout race failed change={change_result.get('error')} "
            f"logout={logout_result.get('error')}"
        )

    sessions = _sessions(settings, int(prepared["user_id"]))
    by_id = {item["id"]: item for item in sessions}
    replacement_id = int(change_result["session_id"])
    assertions = {
        "logout_discovered_session_then_waited_for_user": logout_waited,
        "logout_completed_without_deadlock": logout_result.get("completed") is True,
        "no_extra_session_created": len(sessions) == 3,
        "both_prechange_sessions_revoked": (
            by_id[prepared["first"].session_id]["revoked_at"] is not None
            and by_id[prepared["second"].session_id]["revoked_at"] is not None
        ),
        "phone_change_replacement_active": by_id[replacement_id]["revoked_at"] is None,
    }
    return {
        "user_id": prepared["user_id"],
        "sessions": sessions,
        "logout_result": logout_result,
        "assertions": assertions,
    }


def run_probe() -> dict[str, object]:
    settings = build_isolated_settings(enable_enqueue=True)
    session_factory = get_session_factory(settings.test_database_url)
    reverse_refresh = _run_reverse_refresh(session_factory, settings)
    logout_race = _run_logout_race(session_factory, settings)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    all_assertions = list(reverse_refresh["assertions"].values()) + list(
        logout_race["assertions"].values()
    )
    return {
        "task_id": "PHONE-CHANGE-001-FIX-2",
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "mode": "real MySQL deterministic reverse-order concurrency",
        },
        "reverse_refresh": reverse_refresh,
        "logout_race": logout_race,
        "status": "PASS" if all(all_assertions) else "FAIL",
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
    print(
        json.dumps(
            {
                "reverse_refresh": evidence["reverse_refresh"]["assertions"],
                "logout_race": evidence["logout_race"]["assertions"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if evidence["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
