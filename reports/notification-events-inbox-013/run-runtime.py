"""Collect scoped AUTH001 runtime evidence for NOTIFICATION-EVENTS-INBOX-013.

All business mutations use authenticated API calls. Database access is read-only
and is used only to bind API results to persisted notifications, deliveries,
work-order history, audits, and the current Alembic revision.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import site
import subprocess
import sys
import time
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    sys.path.insert(0, str(candidate))
site.addsitedir(str(REPO / ".venv" / "Lib" / "site-packages"))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "NOTIFICATION-EVENTS-INBOX-013"
API = "http://127.0.0.1:8083/api/v1"
SOURCE = REPO / "tmp" / "resident-wo-paging-actions-012" / "context.json"
PRIVATE_DIR = REPO / "tmp" / "notification-events-inbox-013"
PRIVATE_CONTEXT = PRIVATE_DIR / "context.json"
PUBLIC_EVIDENCE = Path(__file__).with_name("runtime-evidence.json")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
    if match is None:
        raise RuntimeError("Unsupported Development challenge format")
    return str(int(match.group(1)) + int(match.group(2)))


def _error_code(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return str(error.get("code")) if isinstance(error, dict) else None


def _principal_id(value: object) -> int:
    if isinstance(value, dict):
        value = value.get("user_id") or value.get("id")
    return int(value)


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=30.0)
        self.requests: list[dict[str, object]] = []

    def close(self) -> None:
        self.http.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        label: str,
        token: str | None = None,
        body: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        merged_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            merged_headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(
            method,
            path,
            headers=merged_headers,
            json=body,
            params=params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
        self.requests.append(
            {
                "label": label,
                "method": method,
                "path": path,
                "params": sorted((params or {}).keys()),
                "status": response.status_code,
                "error_code": _error_code(payload),
                "request_id_hash": _hash(request_id),
            }
        )
        if response.status_code not in expected:
            raise RuntimeError(
                f"{label} failed: HTTP {response.status_code} code={_error_code(payload)}"
            )
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict[str, object]:
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


def _guard():
    settings = build_isolated_settings()
    database = make_url(settings.test_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = (
        database.host,
        database.port,
        database.database,
        settings.app_env,
        settings.celery_task_queue,
        broker.path.lstrip("/"),
    )
    expected = (
        "127.0.0.1",
        3307,
        "property_ai_auth001_test",
        "test",
        ISOLATED_QUEUE,
        ISOLATED_VHOST,
    )
    if actual != expected:
        raise RuntimeError(f"AUTH001 guard mismatch: {actual!r}")
    return settings


def _notification_page(
    client: Client,
    token: str,
    *,
    label: str,
    after_id: int = 0,
    limit: int = 100,
) -> dict[str, object]:
    return client.data(
        "GET",
        "/notifications",
        token=token,
        params={"after_id": after_id, "limit": limit},
        label=label,
    )


def _wait_assigned(
    client: Client,
    token: str,
    work_order_id: int,
) -> dict[str, object]:
    last: dict[str, object] = {}
    for attempt in range(100):
        last = client.data(
            "GET",
            f"/work-orders/{work_order_id}",
            token=token,
            label=f"assigned-poll-{attempt}",
        )
        work_order = last.get("work_order") or {}
        if work_order.get("status") == "ASSIGNED" and last.get("assignments"):
            return last
        if work_order.get("status") not in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}:
            raise RuntimeError(f"Unexpected auto-dispatch status: {work_order.get('status')}")
        time.sleep(0.25)
    raise RuntimeError(f"Automatic dispatch timed out: {last!r}")


def _ensure_on_duty(
    client: Client,
    *,
    token: str,
    tenant_id: int,
    property_id: int,
) -> dict[str, object]:
    today = client.data(
        "GET",
        "/staff/attendance/today",
        token=token,
        params={"tenant_id": tenant_id, "property_id": property_id},
        label="staff-attendance-before",
    )
    daily = today.get("daily_status")
    if not isinstance(daily, dict):
        return client.data(
            "POST",
            "/staff/attendance/checkin",
            token=token,
            body={"tenant_id": tenant_id, "property_id": property_id},
            label="staff-checkin",
        )
    if daily.get("on_duty") != "ON_DUTY":
        return client.data(
            "PATCH",
            "/staff/attendance/status",
            token=token,
            body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "on_duty": "ON_DUTY",
            },
            label="staff-on-duty",
        )
    return today


def _concurrent_mark_read(
    *,
    run_id: str,
    token: str,
    notification_id: int,
) -> list[dict[str, object]]:
    def one(index: int) -> dict[str, object]:
        response = httpx.post(
            f"{API}/notifications/{notification_id}/read",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Request-Id": f"{run_id}-mark-read-race-{index}",
            },
            timeout=30.0,
        )
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return {
            "status": response.status_code,
            "error_code": _error_code(payload),
            "notification_id": None if not isinstance(data, dict) else data.get("id"),
            "read_at": None if not isinstance(data, dict) else data.get("read_at"),
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(one, (1, 2)))
    if [item["status"] for item in results] != [200, 200]:
        raise RuntimeError(f"Concurrent mark-read did not both succeed: {results!r}")
    timestamps = {item["read_at"] for item in results}
    if None in timestamps or len(timestamps) != 1:
        raise RuntimeError(f"Concurrent mark-read did not preserve one timestamp: {results!r}")
    return results


def _read_database(settings, *, work_order_id: int, announcement_id: int, read_id: int):
    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            work_order = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, reporter_user_id, status, "
                        "process_reason, version, current_assignment_id, closed_at "
                        "FROM work_orders WHERE id=:id"
                    ),
                    {"id": work_order_id},
                ).mappings().one()
            )
            logs = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, actor_user_id, actor_type, from_status, to_status, "
                        "process_reason, event_code, request_id, created_at "
                        "FROM work_order_logs WHERE work_order_id=:id ORDER BY id"
                    ),
                    {"id": work_order_id},
                ).mappings()
            ]
            assignments = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, employee_id, assigned_by_user_id, status, assigned_at, "
                        "accept_at, ended_at, reason FROM work_order_assignments "
                        "WHERE work_order_id=:id ORDER BY id"
                    ),
                    {"id": work_order_id},
                ).mappings()
            ]
            notifications = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT n.id, n.user_id, n.tenant_id, n.property_id, n.title, "
                        "n.resource_type, n.resource_id, n.read_at, n.created_at, "
                        "d.id AS delivery_id, d.channel, d.status AS delivery_status, "
                        "d.attempt, d.delivered_at "
                        "FROM notifications n LEFT JOIN notification_deliveries d "
                        "ON d.notification_id=n.id "
                        "WHERE (n.resource_type='WORK_ORDER' AND n.resource_id=:work_order_id) "
                        "OR (n.resource_type='ANNOUNCEMENT' AND n.resource_id=:announcement_id) "
                        "ORDER BY n.id, d.id"
                    ),
                    {
                        "work_order_id": work_order_id,
                        "announcement_id": announcement_id,
                    },
                ).mappings()
            ]
            announcement = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, title, status, ai_draft, "
                        "created_by_user_id, reviewed_by_user_id, published_by_user_id, "
                        "published_at FROM announcements WHERE id=:id"
                    ),
                    {"id": announcement_id},
                ).mappings().one()
            )
            audits = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, actor_user_id, tenant_id, property_id, action, "
                        "resource_type, resource_id, request_id, created_at "
                        "FROM audit_logs WHERE (resource_type='WORK_ORDER' AND resource_id=:work_order_id) "
                        "OR (resource_type='ANNOUNCEMENT' AND resource_id=:announcement_id) "
                        "ORDER BY id"
                    ),
                    {
                        "work_order_id": work_order_id,
                        "announcement_id": announcement_id,
                    },
                ).mappings()
            ]
            read_row = dict(
                connection.execute(
                    text(
                        "SELECT id, user_id, read_at FROM notifications WHERE id=:id"
                    ),
                    {"id": read_id},
                ).mappings().one()
            )
            cross_scope_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM notifications WHERE "
                        "((resource_type='WORK_ORDER' AND resource_id=:work_order_id) "
                        "OR (resource_type='ANNOUNCEMENT' AND resource_id=:announcement_id)) "
                        "AND (tenant_id<>:tenant_id OR property_id<>:property_id)"
                    ),
                    {
                        "work_order_id": work_order_id,
                        "announcement_id": announcement_id,
                        "tenant_id": int(work_order["tenant_id"]),
                        "property_id": int(work_order["property_id"]),
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()
    return {
        "revision": revision,
        "work_order": work_order,
        "logs": logs,
        "assignments": assignments,
        "notifications": notifications,
        "announcement": announcement,
        "audits": audits,
        "read_row": read_row,
        "cross_scope_count": cross_scope_count,
    }


def _wait_delivery_terminal(settings, *, work_order_id: int, announcement_id: int) -> None:
    for _attempt in range(80):
        evidence = _read_database(
            settings,
            work_order_id=work_order_id,
            announcement_id=announcement_id,
            read_id=_wait_delivery_terminal.read_id,
        )
        states = [item["delivery_status"] for item in evidence["notifications"]]
        if states and all(state in {"SUCCEEDED", "FAILED"} for state in states):
            return
        time.sleep(0.25)
    raise RuntimeError("Notification deliveries did not reach terminal states")


_wait_delivery_terminal.read_id = 0


def main() -> None:
    settings = _guard()
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    run_id = f"ntf013-{secrets.token_hex(6)}"
    client = Client(run_id)
    try:
        health = client.data("GET", "/healthz", label="healthz")
        ready = client.data("GET", "/readyz", label="readyz")
        phones = source["phones"]
        sessions = {
            name: client.sms_login(str(phones[name]), name)
            for name in ("primary", "staff", "manager")
        }
        tokens = {name: str(value["access_token"]) for name, value in sessions.items()}
        me = {
            name: client.data("GET", "/me", token=tokens[name], label=f"{name}-me")
            for name in tokens
        }
        for name in me:
            expected_user_id = _principal_id(source["identities"][name])
            if int(me[name]["user_id"]) != expected_user_id:
                raise RuntimeError(f"{name} authenticated as an unexpected synthetic user")

        tenant_id = int(source["org"]["tenant_id"])
        property_id = int(source["org"]["property_id"])
        house_id = int(source["org"]["house_id"])
        employee_id = int(source["org"]["employee_id"])
        attendance = _ensure_on_duty(
            client,
            token=tokens["staff"],
            tenant_id=tenant_id,
            property_id=property_id,
        )

        before = {
            name: _notification_page(
                client,
                tokens[name],
                label=f"{name}-notifications-before",
            )
            for name in tokens
        }
        before_ids = {
            name: {int(item["id"]) for item in data.get("items", [])}
            for name, data in before.items()
        }

        marker = f"{run_id}:notification event lifecycle"
        created = client.data(
            "POST",
            "/work-orders",
            token=tokens["primary"],
            headers={"Idempotency-Key": f"{run_id}-create"},
            body={"house_id": house_id, "text": marker},
            label="work-order-create",
        )
        work_order_id = int(created["id"])
        detail = _wait_assigned(client, tokens["primary"], work_order_id)
        assignment = (detail.get("assignments") or [])[-1]
        if int(assignment["employee_id"]) != employee_id:
            raise RuntimeError("Automatic dispatch selected an unexpected synthetic employee")
        version = int(detail["work_order"]["version"])

        accepted = client.data(
            "POST",
            f"/staff/work-orders/{work_order_id}/accept",
            token=tokens["staff"],
            body={"version": version},
            label="work-order-accept",
        )
        started = client.data(
            "POST",
            f"/staff/work-orders/{work_order_id}/start",
            token=tokens["staff"],
            body={"version": int(accepted["version"])},
            label="work-order-start",
        )
        escalated = client.data(
            "POST",
            f"/staff/work-orders/{work_order_id}/escalate",
            token=tokens["staff"],
            body={
                "process_reason": "WAITING_PARTS",
                "reason": f"Synthetic scoped escalation {run_id}",
                "version": int(started["version"]),
            },
            label="work-order-escalate",
        )
        continued = client.data(
            "POST",
            f"/supervisor/work-orders/{work_order_id}/decide",
            token=tokens["manager"],
            body={
                "decision": "CONTINUE",
                "reason": f"Synthetic scoped continue {run_id}",
                "version": int(escalated["version"]),
            },
            label="work-order-decide-continue",
        )
        receipted = client.data(
            "POST",
            f"/staff/work-orders/{work_order_id}/receipts",
            token=tokens["staff"],
            body={
                "problem_type": "ELECTRICAL",
                "method": f"Synthetic notification verification {run_id}",
                "materials": None,
                "fee_flag": "NONE",
                "resolved": True,
                "before_file_ids": [],
                "after_file_ids": [],
                "version": int(continued["version"]),
            },
            label="work-order-receipt",
        )
        closed = client.data(
            "POST",
            f"/work-orders/{work_order_id}/confirm",
            token=tokens["primary"],
            body={"version": int(receipted["version"])},
            label="work-order-confirm",
        )
        if closed.get("status") != "CLOSED":
            raise RuntimeError("Work order did not close through the normal lifecycle")

        title = f"Notification verification {run_id}"
        announcement = client.data(
            "POST",
            "/admin/announcements",
            token=tokens["manager"],
            body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "title": title,
                "content": f"Synthetic scoped announcement for {run_id}",
                "ai_draft": False,
                "scope_type": "PROPERTY",
                "audience": "RESIDENT",
            },
            label="announcement-create",
        )
        announcement_id = int(announcement["id"])
        submitted = client.data(
            "POST",
            f"/admin/announcements/{announcement_id}/submit-review",
            token=tokens["manager"],
            label="announcement-submit-review",
        )
        if submitted.get("status") != "PENDING_REVIEW":
            raise RuntimeError("Announcement did not enter review")
        published = client.data(
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=tokens["manager"],
            body={"decision": "APPROVE"},
            label="announcement-publish",
        )
        if published.get("status") != "PUBLISHED":
            raise RuntimeError("Announcement did not publish")

        after: dict[str, dict[str, object]] = {}
        for attempt in range(80):
            after = {
                name: _notification_page(
                    client,
                    tokens[name],
                    label=f"{name}-notifications-after-{attempt}",
                )
                for name in tokens
            }
            resident_new = [
                item
                for item in after["primary"].get("items", [])
                if int(item["id"]) not in before_ids["primary"]
            ]
            if (
                any(
                    item.get("resource_type") == "WORK_ORDER"
                    and int(item.get("resource_id") or 0) == work_order_id
                    for item in resident_new
                )
                and any(
                    item.get("resource_type") == "ANNOUNCEMENT"
                    and int(item.get("resource_id") or 0) == announcement_id
                    for item in resident_new
                )
            ):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("New work-order and announcement notifications were not visible")

        new_items = {
            name: [
                item
                for item in data.get("items", [])
                if int(item["id"]) not in before_ids[name]
            ]
            for name, data in after.items()
        }
        work_order_items = [
            item
            for item in new_items["primary"]
            if item.get("resource_type") == "WORK_ORDER"
            and int(item.get("resource_id") or 0) == work_order_id
        ]
        announcement_items = [
            item
            for item in new_items["primary"]
            if item.get("resource_type") == "ANNOUNCEMENT"
            and int(item.get("resource_id") or 0) == announcement_id
        ]
        if len(work_order_items) < 7 or len(announcement_items) != 1:
            raise RuntimeError("Resident event coverage was incomplete or duplicated")
        read_target = min(work_order_items, key=lambda item: int(item["id"]))
        read_id = int(read_target["id"])

        manager_denied_status, _manager_denied = client.call(
            "POST",
            f"/notifications/{read_id}/read",
            token=tokens["manager"],
            label="manager-read-resident-notification-denied",
            expected=(404,),
        )
        race = _concurrent_mark_read(
            run_id=run_id,
            token=tokens["primary"],
            notification_id=read_id,
        )
        repeated = client.data(
            "POST",
            f"/notifications/{read_id}/read",
            token=tokens["primary"],
            label="resident-read-repeat",
        )
        if repeated.get("read_at") != race[0]["read_at"]:
            raise RuntimeError("Repeat mark-read changed the persisted timestamp")

        staff_first = _notification_page(
            client,
            tokens["staff"],
            label="staff-pagination-first",
            limit=20,
        )
        first_ids = [int(item["id"]) for item in staff_first.get("items", [])]
        cursor = staff_first.get("next_cursor")
        if len(first_ids) != 20 or cursor is None or first_ids != sorted(first_ids, reverse=True):
            raise RuntimeError("Staff notification first page did not expose a sound cursor")
        staff_second = _notification_page(
            client,
            tokens["staff"],
            label="staff-pagination-second",
            after_id=int(cursor),
            limit=20,
        )
        second_ids = [int(item["id"]) for item in staff_second.get("items", [])]
        if (
            not second_ids
            or second_ids != sorted(second_ids, reverse=True)
            or set(first_ids) & set(second_ids)
            or any(value >= int(cursor) for value in second_ids)
        ):
            raise RuntimeError("Staff notification continuation overlapped or reversed")

        validation = []
        for label, params in (
            ("after-negative", {"after_id": -1, "limit": 20}),
            ("limit-zero", {"after_id": 0, "limit": 0}),
            ("limit-too-large", {"after_id": 0, "limit": 101}),
        ):
            status, payload = client.call(
                "GET",
                "/notifications",
                token=tokens["primary"],
                params=params,
                label=f"notification-validation-{label}",
                expected=(422,),
            )
            validation.append({"case": label, "status": status, "code": _error_code(payload)})

        relogin = client.sms_login(str(phones["primary"]), "primary-relogin")
        relogin_page = _notification_page(
            client,
            str(relogin["access_token"]),
            label="primary-notifications-after-relogin",
        )
        persisted = next(
            (item for item in relogin_page.get("items", []) if int(item["id"]) == read_id),
            None,
        )
        if not isinstance(persisted, dict) or persisted.get("read_at") != race[0]["read_at"]:
            raise RuntimeError("Read state did not survive normal re-login")

        _wait_delivery_terminal.read_id = read_id
        _wait_delivery_terminal(
            settings,
            work_order_id=work_order_id,
            announcement_id=announcement_id,
        )
        database = _read_database(
            settings,
            work_order_id=work_order_id,
            announcement_id=announcement_id,
            read_id=read_id,
        )
        if database["revision"] != "20260919_0009":
            raise RuntimeError("AUTH001 revision changed")
        if database["cross_scope_count"] != 0:
            raise RuntimeError("New notification resources crossed tenant/property scope")
        if database["work_order"]["status"] != "CLOSED":
            raise RuntimeError("Database work order state is not CLOSED")
        if database["announcement"]["status"] != "PUBLISHED":
            raise RuntimeError("Database announcement state is not PUBLISHED")
        if database["read_row"]["read_at"] is None:
            raise RuntimeError("Database read state was not persisted")

        private = {
            "task_id": TASK_ID,
            "run_id": run_id,
            "phones": {name: phones[name] for name in ("primary", "staff", "manager")},
            "identities": {name: int(me[name]["user_id"]) for name in me},
            "org": {
                "tenant_id": tenant_id,
                "property_id": property_id,
                "house_id": house_id,
                "employee_id": employee_id,
            },
            "objects": {
                "work_order_id": work_order_id,
                "announcement_id": announcement_id,
                "resident_read_notification_id": read_id,
                "resident_work_order_notification_ids": [int(item["id"]) for item in work_order_items],
                "resident_announcement_notification_id": int(announcement_items[0]["id"]),
            },
        }
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        PRIVATE_CONTEXT.write_text(
            json.dumps(private, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        public = {
            "task_id": TASK_ID,
            "captured_at": datetime.now(UTC).isoformat(),
            "code_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "run_id": run_id,
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": database["revision"],
                "rabbit_vhost": ISOLATED_VHOST,
                "rabbit_queue": ISOLATED_QUEUE,
                "providers": "DEVELOPMENT_SMS_AND_FAKE_AI_AND_DEVELOPMENT_NOTIFICATION",
                "health": health,
                "ready_status": ready.get("status"),
            },
            "identities": {name: int(me[name]["user_id"]) for name in me},
            "objects": {
                "work_order_id": work_order_id,
                "announcement_id": announcement_id,
                "resident_read_notification_id": read_id,
                "marker_hash": _hash(marker),
            },
            "attendance": {
                "business_date": attendance.get("business_date"),
                "on_duty": (attendance.get("daily_status") or {}).get("on_duty"),
            },
            "lifecycle": {
                "auto_assignment_employee_id": int(assignment["employee_id"]),
                "final_status": closed["status"],
                "announcement_status": published["status"],
                "log_events": [item["event_code"] for item in database["logs"]],
            },
            "new_notifications": {
                name: [
                    {
                        "id": int(item["id"]),
                        "tenant_id": int(item["tenant_id"]),
                        "property_id": int(item["property_id"]),
                        "title": item["title"],
                        "resource_type": item["resource_type"],
                        "resource_id": int(item["resource_id"]),
                        "read": item.get("read_at") is not None,
                    }
                    for item in items
                ]
                for name, items in new_items.items()
            },
            "pagination": {
                "first_ids": first_ids,
                "first_cursor": int(cursor),
                "second_ids": second_ids,
                "second_cursor": staff_second.get("next_cursor"),
                "no_overlap": not bool(set(first_ids) & set(second_ids)),
                "strictly_descending": first_ids == sorted(first_ids, reverse=True)
                and second_ids == sorted(second_ids, reverse=True),
            },
            "read_state": {
                "anti_idor_status": manager_denied_status,
                "concurrent_statuses": [item["status"] for item in race],
                "same_timestamp": race[0]["read_at"] == race[1]["read_at"],
                "repeat_timestamp_stable": repeated.get("read_at") == race[0]["read_at"],
                "relogin_persisted": persisted.get("read_at") == race[0]["read_at"],
            },
            "validation": validation,
            "database": database,
            "requests": client.requests,
            "checks": {
                "normal_sms_login": True,
                "automatic_dispatch_not_manual": True,
                "same_work_order_lifecycle_closed": True,
                "announcement_governance_published": True,
                "resource_scope_consistent": database["cross_scope_count"] == 0,
                "deliveries_terminal": all(
                    item["delivery_status"] in {"SUCCEEDED", "FAILED"}
                    for item in database["notifications"]
                ),
                "pagination_boundary_valid": True,
                "anti_idor_and_idempotent_read": True,
                "refresh_relogin_history_consistent": True,
            },
            "boundaries": {
                "page_evidence_in_separate_artifact": True,
                "direct_database_business_write": False,
                "seed_or_dev_token_login": False,
                "migration_run": False,
                "shared_vhost_touched": False,
                "external_provider_exactly_once_claimed": False,
                "real_qwen_or_bge_m3_claimed": False,
                "production_claimed": False,
                "phones_tokens_and_otp_persisted_publicly": False,
            },
        }
        PUBLIC_EVIDENCE.write_text(
            json.dumps(public, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "run_id": run_id,
                    "work_order_id": work_order_id,
                    "announcement_id": announcement_id,
                    "evidence": str(PUBLIC_EVIDENCE.relative_to(REPO)),
                }
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
