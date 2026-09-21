"""Prepare and verify one additive synthetic Incident through official APIs.

The script deliberately refuses to start when a private run-state file already
exists. A partial run must be inspected and resumed deliberately; blindly
rerunning it would create another set of business objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy.engine import make_url

from auth001_isolated_runtime import (
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


REPO = Path(__file__).resolve().parents[2]
PRIVATE_SOURCE = REPO / "tmp" / "auth-password-change" / "context.json"
PRIVATE_DIR = REPO / "tmp" / "incident-management-009"
PRIVATE_STATE = PRIVATE_DIR / "context.json"
PUBLIC_EVIDENCE = Path(__file__).resolve().parent / "api-runtime-evidence.json"
API = "http://127.0.0.1:8083/api/v1"
INCIDENT_THRESHOLD = 5


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
    if match is None:
        raise RuntimeError("Unsupported visual challenge prompt")
    return str(int(match.group(1)) + int(match.group(2)))


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=20.0)
        self.request_ids: list[str] = []

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        label: str,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        self.request_ids.append(request_id)
        request_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(
            method,
            path,
            headers=request_headers,
            json=body if body is not None else None,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
        if response.status_code not in expected:
            error = payload.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            raise RuntimeError(f"{label} failed: HTTP {response.status_code} code={code}")
        return response.status_code, payload

    def data(self, *args, **kwargs):
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"{kwargs.get('label', 'request')} returned no data object")
        return data

    def sms_login(self, phone: str, label: str) -> dict[str, object]:
        challenge = self.data(
            "POST",
            "/auth/challenge",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-challenge",
        )
        self.data(
            "POST",
            "/auth/sms/send",
            body={
                "phone": phone,
                "purpose": "login",
                "challenge_id": challenge["challenge_id"],
                "challenge_answer": _challenge_answer(str(challenge["prompt"])),
            },
            label=f"{label}-sms-send",
        )
        inbox = self.data(
            "POST",
            "/auth/sms/test-inbox",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-sms-inbox",
        )
        return self.data(
            "POST",
            "/auth/sms/login",
            body={"phone": phone, "code": inbox["code"]},
            label=f"{label}-sms-login",
        )


def _save_private(state: dict[str, object]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _assert_isolated_target() -> None:
    settings = build_isolated_settings()
    database = make_url(settings.test_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = {
        "host": database.host,
        "port": database.port,
        "database": database.database,
        "app_env": settings.app_env,
        "queue": settings.celery_task_queue,
        "vhost": broker.path.lstrip("/"),
        "incident_threshold": settings.incident_threshold,
    }
    expected = {
        "host": "127.0.0.1",
        "port": 3307,
        "database": "property_ai_auth001_test",
        "app_env": "test",
        "queue": ISOLATED_QUEUE,
        "vhost": ISOLATED_VHOST,
        "incident_threshold": INCIDENT_THRESHOLD,
    }
    if actual != expected:
        raise RuntimeError(f"AUTH001 target guard rejected runtime settings: {actual}")


def _wait_for_order(
    client: Client,
    token: str,
    order_id: int,
    *,
    require_incident: bool,
) -> dict[str, object]:
    last: dict[str, object] = {}
    for attempt in range(80):
        detail = client.data(
            "GET",
            f"/work-orders/{order_id}",
            token=token,
            label=f"order-{order_id}-poll-{attempt}",
        )
        work_order = detail.get("work_order") or {}
        if isinstance(work_order, dict):
            last = work_order
            if require_incident and work_order.get("incident_id"):
                return work_order
            if not require_incident and work_order.get("status") not in {
                "PENDING_ANALYSIS",
                "PENDING_ASSIGNMENT",
            }:
                return work_order
        time.sleep(0.25)
    raise RuntimeError(
        f"Work order {order_id} did not reach the expected automatic state; "
        f"last_status={last.get('status')} incident={last.get('incident_id')}"
    )


def main() -> None:
    if PRIVATE_STATE.exists():
        raise RuntimeError(
            "A prior INCIDENT-MGMT-UI-009 private run state already exists; "
            "inspect it instead of creating duplicate business objects"
        )
    _assert_isolated_target()
    private_source = json.loads(PRIVATE_SOURCE.read_text(encoding="utf-8"))
    run_id = f"incident-mgmt-{secrets.token_hex(5)}"
    client = Client(run_id)
    client.call("GET", "/readyz", label="api-ready")
    phones = private_source["phones"]
    org = private_source["org"]
    tenant_id = int(org["tenant_id"])
    property_id = int(org["property_id"])
    house_id = int(org["house_id"])
    employee_id = int(private_source["employee_id"])

    sessions = {
        name: client.sms_login(str(phones[name]), name)
        for name in ("primary", "secondary", "manager", "staff")
    }
    tokens = {name: str(value["access_token"]) for name, value in sessions.items()}
    state: dict[str, object] = {
        "run_id": run_id,
        "phones": phones,
        "sessions": sessions,
        "org": org,
        "employee_id": employee_id,
        "work_order_ids": [],
    }
    _save_private(state)

    houses = client.data(
        "GET",
        "/me/houses",
        token=tokens["primary"],
        label="resident-houses-before",
    ).get("items") or []
    relation_created = not any(int(item["house_id"]) == house_id for item in houses)
    bind_request_id: int | None = None
    if relation_created:
        mine = client.data(
            "GET",
            "/resident-bind-requests/mine",
            token=tokens["primary"],
            label="resident-bind-list",
        ).get("items") or []
        pending = next(
            (
                item
                for item in mine
                if int(item["house_id"]) == house_id and item["status"] == "PENDING"
            ),
            None,
        )
        if pending is None:
            pending = client.data(
                "POST",
                "/resident-bind-requests",
                token=tokens["primary"],
                body={"house_id": house_id, "relation_type": "OWNER"},
                label="resident-bind-create",
            )
        bind_request_id = int(pending["id"])
        client.data(
            "POST",
            f"/admin/resident-bind-requests/{bind_request_id}/approve",
            token=tokens["manager"],
            body={},
            label="resident-bind-approve",
        )

    catalog = client.data(
        "GET",
        f"/admin/employee-catalog?property_id={property_id}&tenant_id={tenant_id}",
        token=tokens["manager"],
        label="employee-catalog-before",
    )
    duty = next((item for item in catalog.get("duties", []) if item["code"] == "MAINTENANCE"), None)
    if duty is None:
        duty = client.data(
            "POST",
            "/admin/duties",
            token=tokens["manager"],
            body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "code": "MAINTENANCE",
                "name": f"合成维修职责-{run_id[-6:]}",
            },
            label="create-maintenance-duty",
        )
    skill = next((item for item in catalog.get("skills", []) if item["code"] == "ELECTRICAL"), None)
    if skill is None:
        skill = client.data(
            "POST",
            "/admin/skills",
            token=tokens["manager"],
            body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "code": "ELECTRICAL",
                "name": f"合成电气技能-{run_id[-6:]}",
            },
            label="create-electrical-skill",
        )
    client.data(
        "PATCH",
        f"/admin/employees/{employee_id}",
        token=tokens["manager"],
        body={
            "status": "ACTIVE",
            "max_capacity": 5,
            "add_duty_ids": [int(duty["id"])],
            "add_skill_ids": [int(skill["id"])],
        },
        label="configure-employee",
    )

    today = client.data(
        "GET",
        f"/staff/attendance/today?property_id={property_id}&tenant_id={tenant_id}",
        token=tokens["staff"],
        label="attendance-before",
    )
    attendance = today.get("attendance")
    daily_status = today.get("daily_status")
    if attendance is None or attendance.get("check_out_at") is not None:
        client.data(
            "POST",
            "/staff/attendance/checkin",
            token=tokens["staff"],
            body={"property_id": property_id, "tenant_id": tenant_id},
            label="staff-checkin",
        )
    elif not isinstance(daily_status, dict) or daily_status.get("on_duty") != "ON_DUTY":
        client.data(
            "PATCH",
            "/staff/attendance/status",
            token=tokens["staff"],
            body={
                "property_id": property_id,
                "tenant_id": tenant_id,
                "on_duty": "ON_DUTY",
            },
            label="staff-return-on-duty",
        )

    work_orders: list[dict[str, object]] = []
    for index in range(1, INCIDENT_THRESHOLD + 1):
        created = client.data(
            "POST",
            "/work-orders",
            token=tokens["primary"],
            body={
                "house_id": house_id,
                "text": f"{run_id} 第{index}户反馈同楼厨房插座持续冒火花",
                "tenant_id": tenant_id,
                "property_id": property_id,
            },
            headers={"Idempotency-Key": f"{run_id}-work-order-{index}"},
            label=f"create-work-order-{index}",
        )
        order_id = int(created["id"])
        state["work_order_ids"] = [
            *state["work_order_ids"],
            order_id,
        ]
        _save_private(state)
        work_orders.append(
            _wait_for_order(
                client,
                tokens["primary"],
                order_id,
                require_incident=index == INCIDENT_THRESHOLD,
            )
        )

    incident_id = int(work_orders[-1]["incident_id"])
    state["incident_id"] = incident_id
    _save_private(state)

    manager_list = client.data(
        "GET",
        f"/supervisor/incidents?property_id={property_id}&tenant_id={tenant_id}&limit=50",
        token=tokens["manager"],
        label="manager-incident-list",
    )
    manager_item = next(
        item for item in manager_list["items"] if int(item["id"]) == incident_id
    )
    manager_detail = client.data(
        "GET",
        f"/supervisor/incidents/{incident_id}",
        token=tokens["manager"],
        label="manager-incident-detail",
    )
    staff_list = client.data(
        "GET",
        f"/supervisor/incidents?property_id={property_id}&tenant_id={tenant_id}&limit=50",
        token=tokens["staff"],
        label="staff-participant-list",
    )
    staff_ids = [int(item["id"]) for item in staff_list["items"]]
    secondary_status, secondary_payload = client.call(
        "GET",
        f"/supervisor/incidents/{incident_id}",
        token=tokens["secondary"],
        label="resident-incident-denied",
        expected=(403,),
    )
    mismatch_status, mismatch_payload = client.call(
        "GET",
        f"/supervisor/incidents?property_id={property_id}&tenant_id={tenant_id + 999999}",
        token=tokens["manager"],
        label="tenant-mismatch-denied",
        expected=(422,),
    )

    current_assignments: list[dict[str, object]] = []
    for work_order in work_orders:
        order_id = int(work_order["id"])
        detail = client.data(
            "GET",
            f"/work-orders/{order_id}",
            token=tokens["primary"],
            label=f"verify-current-assignment-{order_id}",
        )
        record = detail.get("work_order") or {}
        assignment_id = record.get("current_assignment_id")
        current = next(
            (
                item
                for item in detail.get("assignments", [])
                if int(item["id"]) == int(assignment_id or 0)
            ),
            None,
        )
        if (
            record.get("status") != "ASSIGNED"
            or current is None
            or int(current["employee_id"]) != employee_id
            or current.get("assigned_by_user_id") is not None
            or not str(current.get("reason") or "").startswith("AUTO_")
        ):
            raise RuntimeError(f"Work order {order_id} was not automatically assigned")
        current_assignments.append(current)

    if int(manager_item["work_order_count"]) != INCIDENT_THRESHOLD:
        raise RuntimeError(
            "Synthetic Incident did not contain exactly the configured threshold "
            f"of {INCIDENT_THRESHOLD} work orders"
        )
    if incident_id not in staff_ids:
        raise RuntimeError("Current staff participant could not see the Incident")
    if any(int(item["id"]) != incident_id for item in staff_list["items"]):
        raise RuntimeError("Staff participant list exposed an unrelated Incident")
    if not manager_detail.get("primary_employee_id"):
        raise RuntimeError("Incident detail did not expose its primary employee")
    if int(manager_detail["primary_employee_id"]) != employee_id:
        raise RuntimeError("Incident primary employee did not match the configured staff")

    evidence = {
        "task_id": "INCIDENT-MGMT-UI-009",
        "run_id": run_id,
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": "9033caa3d5c4c95fe4bc5fc5727a1bd694ee7b49",
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": "20260919_0009",
            "rabbit_vhost": "property_ai_auth001",
            "rabbit_queue": "property_ai.auth001",
            "provider": "DEVELOPMENT_FAKE_AI",
        },
        "objects": {
            "tenant_id": tenant_id,
            "property_id": property_id,
            "house_id": house_id,
            "employee_id": employee_id,
            "work_order_ids": [int(item["id"]) for item in work_orders],
            "incident_id": incident_id,
            "bind_request_id": bind_request_id,
        },
        "checks": {
            "official_sms_sessions": True,
            "resident_relation_ready_through_business_api": True,
            "employee_configured_through_management_api": True,
            "staff_checked_in_through_attendance_api": True,
            "threshold_work_orders_created_through_business_api": True,
            "automatic_analysis_and_assignment": len(current_assignments)
            == INCIDENT_THRESHOLD,
            "incident_work_order_count": int(manager_item["work_order_count"]),
            "manager_list_and_detail": True,
            "current_staff_participant_visible": True,
            "staff_unrelated_incident_count": 0,
            "resident_detail_denied": secondary_status == 403,
            "resident_error_code": secondary_payload["error"]["code"],
            "tenant_mismatch_denied": mismatch_status == 422,
            "tenant_mismatch_error_code": mismatch_payload["error"]["code"],
        },
        "request_ids": client.request_ids,
        "boundaries": {
            "seed_login_used": False,
            "direct_database_business_write": False,
            "migration_run": False,
            "shared_vhost_read_consumed_replayed_or_cleared": False,
            "real_model_claimed": False,
        },
        "private_context": "tmp/incident-management-009/context.json",
        "redaction": "No phone, OTP, token, password, resident text, or secret is emitted.",
    }
    PUBLIC_EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
