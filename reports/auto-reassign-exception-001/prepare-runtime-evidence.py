"""Prepare two eligible synthetic staff through official business APIs only."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402


BASE = "http://127.0.0.1:8083/api/v1"
ACCOUNTS_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts.local.json"
PUBLIC_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts-public.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "prepare-evidence.json"


def _request(
    method: str,
    path: str,
    *,
    request_id: str,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "X-Request-Id": request_id}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = json.loads(exc.read().decode("utf-8"))
        error = response_body.get("error", {})
        raise RuntimeError(
            f"{method} {path} returned {exc.code} {error.get('code', 'UNKNOWN')}"
        ) from exc


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", prompt.strip())
    if match is None:
        raise RuntimeError("Development challenge format was not recognized")
    return str(int(match.group(1)) + int(match.group(2)))


def _login(phone: str, request_id: str) -> str:
    _, challenge_response = _request(
        "POST",
        "/auth/challenge",
        request_id=f"{request_id}-challenge",
        payload={"phone": phone, "purpose": "login"},
    )
    challenge = challenge_response["data"]
    _request(
        "POST",
        "/auth/sms/send",
        request_id=f"{request_id}-send",
        payload={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": _challenge_answer(challenge["prompt"]),
        },
    )
    _, inbox_response = _request(
        "POST",
        "/auth/sms/test-inbox",
        request_id=f"{request_id}-inbox",
        payload={"phone": phone, "purpose": "login"},
    )
    _, response = _request(
        "POST",
        "/auth/sms/login",
        request_id=f"{request_id}-login",
        payload={"phone": phone, "code": inbox_response["data"]["code"]},
    )
    return str(response["data"]["access_token"])


def _employee_id_for_user(user_id: int) -> int | None:
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT id FROM employees WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).scalar_one_or_none()
            return None if value is None else int(value)
    finally:
        engine.dispose()


def _ensure_attendance(
    *,
    token: str,
    tenant_id: int,
    property_id: int,
    request_prefix: str,
) -> dict[str, object]:
    query = urllib.parse.urlencode(
        {"tenant_id": tenant_id, "property_id": property_id}
    )
    _, response = _request(
        "GET",
        f"/staff/attendance/today?{query}",
        request_id=f"{request_prefix}-today",
        token=token,
    )
    snapshot = response["data"]
    action = "existing"
    if snapshot["attendance"] is None:
        _, response = _request(
            "POST",
            "/staff/attendance/checkin",
            request_id=f"{request_prefix}-checkin",
            token=token,
            payload={"tenant_id": tenant_id, "property_id": property_id},
        )
        snapshot = response["data"]
        action = "checked_in"
    elif snapshot["attendance"]["check_out_at"] is not None:
        raise RuntimeError("Synthetic employee already checked out for this business date")
    if snapshot["daily_status"]["on_duty"] != "ON_DUTY":
        _, response = _request(
            "PATCH",
            "/staff/attendance/status",
            request_id=f"{request_prefix}-on-duty",
            token=token,
            payload={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "on_duty": "ON_DUTY",
            },
        )
        snapshot = response["data"]
        action = f"{action}+on_duty"
    return {
        "employee_id": snapshot["attendance"]["employee_id"],
        "business_date": snapshot["business_date"],
        "on_duty": snapshot["daily_status"]["on_duty"],
        "current_load": snapshot["daily_status"]["current_load"],
        "action": action,
    }


def main() -> int:
    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))
    org = public["org"]
    tenant_id = int(org["tenant_id"])
    property_id = int(org["property_id"])
    run_id = f"auto-reassign-prepare-{uuid4().hex[:10]}"
    admin_token = _login(
        accounts["phones"]["tenant_admin"],
        f"{run_id}-admin-login",
    )
    staff_token = _login(
        accounts["phones"]["staff"],
        f"{run_id}-staff-login",
    )
    staff2_token = _login(
        accounts["phones"]["staff2"],
        f"{run_id}-staff2-login",
    )

    catalog_query = urllib.parse.urlencode(
        {"tenant_id": tenant_id, "property_id": property_id}
    )
    _, catalog_response = _request(
        "GET",
        f"/admin/employee-catalog?{catalog_query}",
        request_id=f"{run_id}-catalog",
        token=admin_token,
    )
    catalog = catalog_response["data"]
    duty_id = next(
        int(item["id"])
        for item in catalog["duties"]
        if item["code"] == "MAINTENANCE" and item["status"] == "ACTIVE"
    )
    skill_id = next(
        int(item["id"])
        for item in catalog["skills"]
        if item["code"] == "ELECTRICAL" and item["status"] == "ACTIVE"
    )

    staff2_user_id = int(public["roles"]["staff2"]["user_id"])
    staff2_employee_id = _employee_id_for_user(staff2_user_id)
    created_staff2 = False
    claimed_staff2 = False
    if staff2_employee_id is None:
        _, created_response = _request(
            "POST",
            "/admin/employees",
            request_id=f"{run_id}-create-staff2",
            token=admin_token,
            payload={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "employee_no": f"REASSIGN-{uuid4().hex[:12].upper()}",
                "duty_ids": [duty_id],
                "skill_ids": [skill_id],
                "max_capacity": 8,
            },
        )
        staff2_employee_id = int(created_response["data"]["id"])
        created_staff2 = True
        _, invite_response = _request(
            "POST",
            f"/admin/employees/{staff2_employee_id}/invites",
            request_id=f"{run_id}-invite-staff2",
            token=admin_token,
            payload={"phone": accounts["phones"]["staff2"]},
        )
        _request(
            "POST",
            "/staff/employee-claim",
            request_id=f"{run_id}-claim-staff2",
            token=staff2_token,
            payload={"token": invite_response["data"]["invite_token"]},
        )
        claimed_staff2 = True

    _, detail_response = _request(
        "GET",
        f"/admin/employees/{staff2_employee_id}",
        request_id=f"{run_id}-staff2-detail",
        token=admin_token,
    )
    detail = detail_response["data"]
    add_duties = [] if any(item["id"] == duty_id for item in detail["duties"]) else [duty_id]
    add_skills = [] if any(item["id"] == skill_id for item in detail["skills"]) else [skill_id]
    active_property = any(
        item["property_id"] == property_id
        and item["status"] == "ACTIVE"
        and item["valid_to"] is None
        for item in detail["assignments"]
    )
    if not active_property:
        _request(
            "POST",
            f"/admin/employees/{staff2_employee_id}/property-assignments",
            request_id=f"{run_id}-staff2-property",
            token=admin_token,
            payload={"property_id": property_id},
        )
    if add_duties or add_skills:
        _request(
            "PATCH",
            f"/admin/employees/{staff2_employee_id}",
            request_id=f"{run_id}-staff2-capability",
            token=admin_token,
            payload={
                "add_duty_ids": add_duties,
                "add_skill_ids": add_skills,
            },
        )

    staff_attendance = _ensure_attendance(
        token=staff_token,
        tenant_id=tenant_id,
        property_id=property_id,
        request_prefix=f"{run_id}-staff",
    )
    staff2_attendance = _ensure_attendance(
        token=staff2_token,
        tenant_id=tenant_id,
        property_id=property_id,
        request_prefix=f"{run_id}-staff2",
    )
    if staff_attendance["employee_id"] == staff2_attendance["employee_id"]:
        raise RuntimeError("Synthetic staff identities resolved to the same employee")

    result = {
        "task_id": "AUTO-REASSIGN-EXCEPTION-001-PREPARE",
        "captured_at": datetime.now(UTC).isoformat(),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "environment": {
            "api": BASE,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": "20260919_0009",
            "queue": "property_ai.auth001",
            "provider": "DEVELOPMENT",
        },
        "tenant_id": tenant_id,
        "property_id": property_id,
        "house_id": int(org["house_id"]),
        "employees": {
            "staff": staff_attendance,
            "staff2": staff2_attendance,
        },
        "staff2_preparation": {
            "created_employee": created_staff2,
            "claimed_invite": claimed_staff2,
            "added_duty": bool(add_duties),
            "added_skill": bool(add_skills),
            "added_property_assignment": not active_property,
        },
        "boundaries": {
            "official_sms_sessions": True,
            "business_api_writes_only": True,
            "direct_database_write_used": False,
            "shared_database_touched": False,
            "real_model_verified": False,
            "production_v1_passed": False,
        },
        "redaction": "No phone, password, invite token, access token, resident name, or credential is emitted.",
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
