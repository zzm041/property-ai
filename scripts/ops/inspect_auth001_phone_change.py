"""Read-only, redacted evidence for one AUTH001 self-service phone change."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.domain.phone import mask_phone  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def inspect_phone_change(user_id: int) -> dict[str, object]:
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        user = dict(
            connection.execute(
                text("SELECT id, display_name, status FROM users WHERE id = :user_id"),
                {"user_id": user_id},
            ).mappings().one()
        )
        phones = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, phone_e164, status, verified_at, ended_at, created_at "
                    "FROM user_phones WHERE user_id = :user_id ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        sessions = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, auth_method, expires_at, revoked_at, created_at "
                    "FROM auth_sessions WHERE user_id = :user_id ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        audits = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT action, resource_type, resource_id, request_id, details_json, created_at "
                    "FROM audit_logs WHERE actor_user_id = :user_id "
                    "AND action IN ('AUTH_PHONE_CHANGED', 'AUTH_SESSION_REVOKED') ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        sms_challenges = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT phone_e164, purpose, attempts, max_attempts, consumed_at, created_at "
                    "FROM sms_challenges WHERE purpose = 'change_phone' "
                    "AND phone_e164 IN (SELECT phone_e164 FROM user_phones "
                    "WHERE user_id = :user_id) ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
    engine.dispose()

    for phone in phones:
        phone["phone_masked"] = mask_phone(str(phone.pop("phone_e164")))
    for audit in audits:
        audit["details_json"] = _json_object(audit.get("details_json"))
    for challenge in sms_challenges:
        challenge["phone_masked"] = mask_phone(str(challenge.pop("phone_e164")))
    database = make_url(settings.test_database_url)
    change_audits = [item for item in audits if item["action"] == "AUTH_PHONE_CHANGED"]
    active_phone = next((item for item in phones if item["status"] == "ACTIVE"), None)
    return {
        "task_id": "PHONE-CHANGE-001",
        "database": {
            "host": database.host,
            "port": database.port,
            "name": database.database,
            "migration": revision,
            "mode": "read-only inspection",
        },
        "user_id": user_id,
        "user": user,
        "phones": phones,
        "sessions": sessions,
        "audits": audits,
        "sms_challenges": sms_challenges,
        "assertions": {
            "one_active_phone": sum(item["status"] == "ACTIVE" for item in phones) == 1,
            "ended_phone_retained": any(item["status"] == "ENDED" for item in phones),
            "phone_change_audit_present": bool(change_audits),
            "audit_contains_masked_phones_only": all(
                "****" in str(item.get("details_json", {}).get(key, ""))
                for item in change_audits
                for key in ("old_phone", "new_phone")
            ),
            "revoked_session_present": any(item["revoked_at"] is not None for item in sessions),
            "active_session_present": any(item["revoked_at"] is None for item in sessions),
            "failed_otp_attempt_persisted": any(
                item["attempts"] > 0 for item in sms_challenges
            ),
            "default_label_tracks_active_phone": bool(
                active_phone
                and user["display_name"] == active_phone["phone_masked"]
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = inspect_phone_change(args.user_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["assertions"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
