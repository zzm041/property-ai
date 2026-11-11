"""Read-only, redacted evidence for one AUTH001 WeChat rebind user."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.domain.identity import mask_phone  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _details(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def inspect(user_id: int) -> dict[str, object]:
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
                    "SELECT phone_e164, status, verified_at, ended_at, created_at "
                    "FROM user_phones WHERE user_id = :user_id ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
        bindings = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, appid, openid, unionid, created_at, updated_at "
                    "FROM user_wechats WHERE user_id = :user_id ORDER BY id"
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
                    "AND action IN ('AUTH_WECHAT_BOUND', 'AUTH_WECHAT_REBOUND', 'AUTH_SESSION_REVOKED') "
                    "ORDER BY id"
                ),
                {"user_id": user_id},
            ).mappings()
        ]
    engine.dispose()

    for phone in phones:
        phone["phone_masked"] = mask_phone(str(phone.pop("phone_e164")))
    for binding in bindings:
        binding["openid_fingerprint"] = _fingerprint(str(binding.pop("openid")))
        unionid = binding.pop("unionid")
        binding["unionid_present"] = unionid is not None
        if unionid is not None:
            binding["unionid_fingerprint"] = _fingerprint(str(unionid))
    for audit in audits:
        audit["details_json"] = _details(audit.get("details_json"))
    rebind_audits = [item for item in audits if item["action"] == "AUTH_WECHAT_REBOUND"]
    serialized_audits = json.dumps(audits, ensure_ascii=False, default=_default)
    database = make_url(settings.test_database_url)
    return {
        "task_id": "WECHAT-REBIND-001",
        "database": {
            "host": database.host,
            "port": database.port,
            "name": database.database,
            "migration": revision,
            "mode": "read-only inspection",
        },
        "user": user,
        "phones": phones,
        "bindings": bindings,
        "sessions": sessions,
        "audits": audits,
        "assertions": {
            "one_binding_for_configured_appid": sum(
                item["appid"] == settings.wechat_app_id for item in bindings
            )
            == 1,
            "rebind_audit_present": bool(rebind_audits),
            "revoked_sessions_present": any(
                item["revoked_at"] is not None for item in sessions
            ),
            "active_sms_session_present": any(
                item["revoked_at"] is None and item["auth_method"] == "sms"
                for item in sessions
            ),
            "audit_has_no_phone_or_wechat_identifier": re.search(
                r"\b1[3-9]\d{9}\b|openid|unionid|wx-new-", serialized_audits
            )
            is None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = inspect(args.user_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence["assertions"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
