"""Day 4 cross-process HTTP E2E against Nginx. Fake providers only.

Requires DAY4_BASE_URL. Looks up seed identities from TEST_DATABASE_URL.
Path under test: HTTP/Nginx → API → RabbitMQ → Celery Worker → MySQL/Milvus/MinIO.
PASS is the conjunction of every required assertion. Missing environment is
NOT_RUN (exit 2), never PASS.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

BASE = os.environ.get("DAY4_BASE_URL", "").rstrip("/")
WORKER = os.environ.get("DAY4_WORKER_CONTAINER", "property-ai-day4-worker-day4-1")
OVERALL_PASS = "DAY4_CROSS_PROCESS_E2E=PASS (Fake providers; not live vendor)"
OVERALL_FAIL = "DAY4_CROSS_PROCESS_E2E=FAIL (Fake providers)"
OVERALL_NOT_RUN = "DAY4_CROSS_PROCESS_E2E=NOT_RUN"


class StepError(AssertionError):
    """A required E2E step did not meet its HTTP or business postcondition."""


def citations_match_ingest(
    citations: Any,
    *,
    document_id: int,
    version: int,
) -> bool:
    if not isinstance(citations, list) or not citations:
        return False
    for item in citations:
        if not isinstance(item, dict):
            continue
        if int(item.get("document_id") or 0) == document_id and int(
            item.get("version") or 0
        ) == version:
            return True
    return False


def notifications_include(
    items: Any,
    *,
    resource_type: str,
    resource_id: int,
) -> bool:
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("resource_type") or "") == resource_type
        and int(item.get("resource_id") or 0) == resource_id
        for item in items
    )


def work_order_status(payload: dict[str, Any] | None) -> str:
    data = payload or {}
    nested = data.get("work_order") if isinstance(data.get("work_order"), dict) else {}
    return str(data.get("status") or nested.get("status") or "")


def finalize(failed: bool, evidence: list[str]) -> int:
    print("\n".join(evidence))
    if failed:
        print(OVERALL_FAIL)
        return 1
    print(OVERALL_PASS)
    return 0


class Checks:
    def __init__(self) -> None:
        self.evidence: list[str] = []
        self.failures: list[str] = []

    @property
    def failed(self) -> bool:
        return bool(self.failures)

    def note(self, message: str) -> None:
        self.evidence.append(message)

    def expect(self, ok: bool, message: str) -> None:
        self.note(message)
        if not ok:
            self.failures.append(message)

    def expect_status(
        self,
        response: httpx.Response,
        expected: int,
        label: str,
        *,
        required: bool = True,
    ) -> bool:
        ok = response.status_code == expected
        body = ""
        try:
            body = response.text[:400]
        except Exception:
            body = ""
        body = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted-jwt]", body)
        body = re.sub(r'(access_token"\s*:\s*")[^"]+', r"\1[redacted]", body)
        message = f"{label} status={response.status_code} expected={expected} body={body}"
        if required:
            self.expect(ok, message)
        else:
            self.note(message)
        return ok


def _headers(token: str | None, request_id: str) -> dict[str, str]:
    headers = {"X-Request-Id": request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _lookup_ids() -> dict[str, int]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is required for identity lookup")
    engine = create_engine(url)
    ids: dict[str, int] = {}
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, display_name FROM users WHERE display_name IN "
                "('__DEV_SYSTEM_ADMIN__', '__DEV_PROPERTY_MANAGER__', "
                "'__DEV_RESIDENT__', '__DEV_STAFF__')"
            )
        ).all()
        mapping = {str(name): int(user_id) for user_id, name in rows}
        for key, name in (
            ("system", "__DEV_SYSTEM_ADMIN__"),
            ("manager", "__DEV_PROPERTY_MANAGER__"),
            ("resident", "__DEV_RESIDENT__"),
            ("staff", "__DEV_STAFF__"),
        ):
            if name not in mapping:
                raise SystemExit(f"seed identity {name} is missing")
            ids[key] = mapping[name]
        ids["house_id"] = int(
            connection.execute(text("SELECT id FROM houses ORDER BY id LIMIT 1")).scalar_one()
        )
        ids["property_id"] = int(
            connection.execute(
                text("SELECT property_id FROM houses WHERE id=:id"),
                {"id": ids["house_id"]},
            ).scalar_one()
        )
        employee = connection.execute(
            text("SELECT id FROM employees WHERE user_id=:uid"),
            {"uid": ids["staff"]},
        ).scalar()
        if employee is None:
            raise SystemExit("seed staff employee fixture is missing")
        ids["employee_id"] = int(employee)
    engine.dispose()
    return ids


def _token(client: httpx.Client, user_id: int, checks: Checks, label: str) -> str:
    response = client.post(
        f"{BASE}/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers=_headers(None, f"d4-login-{user_id}"),
    )
    checks.expect_status(response, 200, label)
    token = str((response.json().get("data") or {}).get("access_token") or "")
    checks.expect(bool(token), f"{label} returned access_token")
    return token


def _wait_status(
    client: httpx.Client,
    token: str,
    work_order_id: int,
    wanted: set[str],
    timeout: float,
    checks: Checks,
    label: str,
) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        rid = f"d4-poll-{work_order_id}-{uuid4().hex[:6]}"
        response = client.get(
            f"{BASE}/api/v1/work-orders/{work_order_id}",
            headers=_headers(token, rid),
        )
        if response.status_code == 200:
            last = work_order_status(response.json().get("data") or {})
            if last in wanted:
                checks.note(f"{label} status={last}")
                return last
        time.sleep(1.5)
    checks.expect(False, f"{label} stayed {last}, wanted {wanted}")
    return last


def _worker_running() -> bool:
    listed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return WORKER in listed.stdout.splitlines()


def _worker_inspect_running() -> bool:
    inspected = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", WORKER],
        capture_output=True,
        text=True,
        check=False,
    )
    return inspected.returncode == 0 and inspected.stdout.strip().lower() == "true"


def _control_worker(action: str, checks: Checks) -> None:
    result = subprocess.run(
        ["docker", action, WORKER],
        capture_output=True,
        text=True,
        check=False,
    )
    checks.expect(
        result.returncode == 0,
        f"worker_{action}_rc={result.returncode} err={(result.stderr or '')[-200:]}",
    )
    time.sleep(1.5)
    running = _worker_inspect_running()
    wanted = action == "start"
    checks.expect(running is wanted, f"worker_state_after_{action} running={running}")


def main() -> int:
    if not BASE:
        print("DAY4_BASE_URL is required", file=sys.stderr)
        print(OVERALL_NOT_RUN)
        return 2
    if not os.environ.get("TEST_DATABASE_URL"):
        print("TEST_DATABASE_URL is required", file=sys.stderr)
        print(OVERALL_NOT_RUN)
        return 2
    try:
        ids = _lookup_ids()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        print(OVERALL_NOT_RUN)
        return 2

    checks = Checks()
    worker_was_running = _worker_running()
    with httpx.Client(timeout=30.0) as client:
        try:
            _run_flow(client, ids, checks, worker_was_running)
        finally:
            if worker_was_running:
                restore = subprocess.run(
                    ["docker", "start", WORKER],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                checks.expect(
                    restore.returncode == 0,
                    f"worker_restore_rc={restore.returncode}",
                )
    return finalize(checks.failed, checks.evidence)


def _run_flow(
    client: httpx.Client,
    ids: dict[str, int],
    checks: Checks,
    worker_was_running: bool,
) -> None:
    health = client.get(f"{BASE}/healthz", headers=_headers(None, "d4-healthz"))
    ready = client.get(f"{BASE}/api/v1/readyz", headers=_headers(None, "d4-readyz"))
    checks.expect_status(health, 200, "healthz")
    checks.expect(ready.status_code == 200, f"readyz status={ready.status_code}")
    deps = (ready.json().get("data") or {}).get("dependencies") or {}
    for name in ("llm", "vlm", "asr", "embedding", "reranker"):
        status = (deps.get(name) or {}).get("status")
        checks.expect(
            status != "up",
            f"readyz.{name}={status} must not claim live vendor up",
        )

    public_opsz = client.get(f"{BASE}/api/v1/opsz", headers=_headers(None, "d4-opsz-public"))
    checks.expect(public_opsz.status_code == 401, f"opsz_unauthenticated={public_opsz.status_code}")

    system_token = _token(client, ids["system"], checks, "login_system")
    manager = _token(client, ids["manager"], checks, "login_manager")
    seed_resident = _token(client, ids["resident"], checks, "login_seed_resident")
    staff = _token(client, ids["staff"], checks, "login_staff")

    opsz_manager = client.get(f"{BASE}/api/v1/opsz", headers=_headers(manager, "d4-opsz-mgr"))
    checks.expect_status(opsz_manager, 403, "opsz_manager")
    opsz = client.get(f"{BASE}/api/v1/opsz", headers=_headers(system_token, "d4-opsz"))
    checks.expect_status(opsz, 200, "opsz_system")
    opsz_data = opsz.json().get("data") or {}
    checks.expect("secret" not in str(opsz.json()).casefold(), "opsz_has_no_secret")
    checks.note(f"opsz_queue={opsz_data.get('celery_queue')}")

    wx = client.post(
        f"{BASE}/api/v1/auth/wechat/login",
        json={"code": f"wx-new-day4-{uuid4().hex[:10]}"},
        headers=_headers(None, "d4-wx-orphan"),
    )
    checks.expect_status(wx, 200, "wechat_new")
    orphan = str((wx.json().get("data") or {}).get("access_token") or "")
    checks.expect(bool(orphan), "wechat_new access_token")

    bind = client.post(
        f"{BASE}/api/v1/resident-bind-requests",
        json={"house_id": ids["house_id"], "relation_type": "FAMILY"},
        headers=_headers(orphan, "d4-bind"),
    )
    checks.expect_status(bind, 200, "bind_apply")
    bind_data = bind.json().get("data") or {}
    checks.expect(bind_data.get("status") == "PENDING", f"bind_status={bind_data.get('status')}")
    checks.expect(bind_data.get("id"), "bind_id")
    houses_before = client.get(
        f"{BASE}/api/v1/me/houses",
        headers=_headers(orphan, "d4-houses-before"),
    )
    checks.expect_status(houses_before, 200, "houses_before_approve")
    checks.expect(
        houses_before.json().get("data", {}).get("items") == [],
        "apply_must_not_create_house_relation",
    )

    approved = client.post(
        f"{BASE}/api/v1/admin/resident-bind-requests/{bind_data.get('id')}/approve",
        headers=_headers(manager, "d4-bind-approve"),
    )
    checks.expect_status(approved, 200, "bind_approve")
    checks.expect(
        (approved.json().get("data") or {}).get("status") == "APPROVED",
        "bind_approve_status",
    )
    if approved.status_code != 200:
        checks.expect(False, "new_user_approve_failed_without_seed_fallback")
        return
    resident = orphan

    duplicate_key = f"d4-{uuid4()}"
    created = client.post(
        f"{BASE}/api/v1/work-orders",
        json={"house_id": ids["house_id"], "text": "Kitchen outlet sparking", "category": "ELECTRICAL"},
        headers={**_headers(resident, "d4-wo-create"), "Idempotency-Key": duplicate_key},
    )
    checks.expect_status(created, 200, "create_wo")
    work_order_id = (created.json().get("data") or {}).get("id")
    checks.expect(bool(work_order_id), "create_wo id")
    if not work_order_id:
        return
    work_order_id = int(work_order_id)
    again = client.post(
        f"{BASE}/api/v1/work-orders",
        json={"house_id": ids["house_id"], "text": "Kitchen outlet sparking", "category": "ELECTRICAL"},
        headers={**_headers(resident, "d4-wo-dup"), "Idempotency-Key": duplicate_key},
    )
    checks.expect_status(again, 200, "duplicate_wo")
    checks.expect(
        (again.json().get("data") or {}).get("id") == work_order_id,
        f"duplicate_wo id={(again.json().get('data') or {}).get('id')} original={work_order_id}",
    )

    if worker_was_running:
        _control_worker("stop", checks)
        paused = client.post(
            f"{BASE}/api/v1/work-orders",
            json={"house_id": ids["house_id"], "text": "Worker paused analysis"},
            headers={**_headers(resident, "d4-wo-paused"), "Idempotency-Key": f"d4-pause-{uuid4()}"},
        )
        checks.expect_status(paused, 200, "paused_create")
        paused_id = (paused.json().get("data") or {}).get("id")
        checks.expect(bool(paused_id), "paused_create id")
        time.sleep(3)
        paused_get = client.get(
            f"{BASE}/api/v1/work-orders/{paused_id}",
            headers=_headers(resident, "d4-wo-paused-get"),
        )
        paused_status = work_order_status(paused_get.json().get("data") or {})
        checks.expect(
            paused_status in {"PENDING_ANALYSIS", "CREATED"},
            f"paused_status={paused_status}",
        )
        _control_worker("start", checks)
        if paused_id:
            _wait_status(
                client,
                resident,
                int(paused_id),
                {"PENDING_ASSIGNMENT", "ASSIGNED", "PENDING_DISPATCH"},
                90,
                checks,
                "resumed_after_worker",
            )

    status = _wait_status(
        client,
        resident,
        work_order_id,
        {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"},
        90,
        checks,
        "wo_after_analysis",
    )
    checks.expect(
        status in {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED"},
        f"worker_consumed status={status}",
    )

    checkin = client.post(
        f"{BASE}/api/v1/staff/attendance/checkin",
        json={"property_id": ids["property_id"]},
        headers=_headers(staff, "d4-checkin"),
    )
    today = client.get(
        f"{BASE}/api/v1/staff/attendance/today",
        params={"property_id": ids["property_id"]},
        headers=_headers(staff, "d4-attendance-today"),
    )
    checks.expect_status(today, 200, "attendance_today")
    daily = ((today.json().get("data") or {}).get("daily_status") or {})
    if checkin.status_code not in {200, 409}:
        checks.expect_status(checkin, 200, "checkin")
    checks.expect(daily.get("on_duty") == "ON_DUTY", f"staff_on_duty={daily.get('on_duty')}")
    checks.note(f"checkin={checkin.status_code}")

    assigned = client.post(
        f"{BASE}/api/v1/supervisor/work-orders/{work_order_id}/assign",
        json={"employee_id": ids["employee_id"]},
        headers=_headers(manager, "d4-assign"),
    )
    checks.expect_status(assigned, 200, "assign")
    accepted = client.post(
        f"{BASE}/api/v1/staff/work-orders/{work_order_id}/accept",
        json={},
        headers=_headers(staff, "d4-accept"),
    )
    checks.expect_status(accepted, 200, "accept")
    started = client.post(
        f"{BASE}/api/v1/staff/work-orders/{work_order_id}/start",
        json={},
        headers=_headers(staff, "d4-start"),
    )
    checks.expect_status(started, 200, "start")
    receipt = client.post(
        f"{BASE}/api/v1/staff/work-orders/{work_order_id}/receipts",
        json={"problem_type": "ELECTRICAL", "method": "Replace outlet", "resolved": True},
        headers=_headers(staff, "d4-receipt"),
    )
    checks.expect_status(receipt, 200, "receipt")
    confirm = client.post(
        f"{BASE}/api/v1/work-orders/{work_order_id}/confirm",
        json={},
        headers=_headers(resident, "d4-confirm"),
    )
    checks.expect_status(confirm, 200, "confirm")
    closed = work_order_status(confirm.json().get("data") or {})
    if closed != "CLOSED":
        closed = _wait_status(
            client,
            resident,
            work_order_id,
            {"CLOSED"},
            30,
            checks,
            "final_closed",
        )
    checks.expect(closed == "CLOSED", f"final_status={closed}")

    marker = uuid4().hex[:8]
    knowledge = client.post(
        f"{BASE}/api/v1/admin/knowledge-documents",
        json={
            "property_id": ids["property_id"],
            "title": f"Day4 装修规定 {marker}",
            "document_type": "RULE",
            "visibility": "RESIDENT",
            "source_text": f"装修仅工作日 08:00-18:00。标记 {marker}。",
        },
        headers=_headers(manager, "d4-kb"),
    )
    checks.expect_status(knowledge, 200, "knowledge_ingest")
    kb = knowledge.json().get("data") or {}
    document_id = kb.get("document_id")
    version = kb.get("version")
    checks.expect(bool(document_id and version), f"knowledge ids document_id={document_id} version={version}")
    asked = None
    matched = False
    for _ in range(20):
        time.sleep(2)
        asked = client.post(
            f"{BASE}/api/v1/assistant/query",
            json={"question": f"装修时间是什么？标记 {marker}", "property_id": ids["property_id"]},
            headers=_headers(resident, "d4-ask"),
        )
        if asked.status_code == 200 and citations_match_ingest(
            (asked.json().get("data") or {}).get("citations"),
            document_id=int(document_id or 0),
            version=int(version or 0),
        ):
            matched = True
            break
    if asked is None:
        checks.expect(False, "assistant_no_response")
    else:
        checks.expect_status(asked, 200, "assistant")
    checks.expect(
        matched,
        f"assistant_citations_this_round document_id={document_id} version={version} "
        f"got={(asked.json().get('data') or {}).get('citations') if asked else None}",
    )

    announcement = client.post(
        f"{BASE}/api/v1/admin/announcements",
        json={
            "property_id": ids["property_id"],
            "title": f"停水通知 {marker}",
            "content": "明日上午检修。",
            "audience": "RESIDENT",
        },
        headers=_headers(manager, "d4-ann"),
    )
    checks.expect_status(announcement, 200, "announcement_draft")
    ann_id = (announcement.json().get("data") or {}).get("id")
    checks.expect(bool(ann_id), "announcement id")
    if ann_id:
        submitted = client.post(
            f"{BASE}/api/v1/admin/announcements/{ann_id}/submit-review",
            headers=_headers(manager, "d4-ann-review"),
        )
        checks.expect_status(submitted, 200, "announcement_submit_review")
        published = client.post(
            f"{BASE}/api/v1/admin/announcements/{ann_id}/publish",
            json={"decision": "APPROVE"},
            headers=_headers(manager, "d4-ann-pub"),
        )
        checks.expect_status(published, 200, "announcement_publish")
        checks.expect(
            (published.json().get("data") or {}).get("status") == "PUBLISHED",
            f"announcement_status={(published.json().get('data') or {}).get('status')}",
        )

    notes = client.get(f"{BASE}/api/v1/notifications", headers=_headers(resident, "d4-notes"))
    checks.expect_status(notes, 200, "notifications")
    items = (notes.json().get("data") or {}).get("items") or []
    checks.expect(
        notifications_include(items, resource_type="RESIDENT_BIND_REQUEST", resource_id=int(bind_data.get("id") or 0)),
        "notification_bind_this_round",
    )
    checks.expect(
        notifications_include(items, resource_type="WORK_ORDER", resource_id=work_order_id),
        "notification_work_order_this_round",
    )
    if ann_id:
        checks.expect(
            notifications_include(items, resource_type="ANNOUNCEMENT", resource_id=int(ann_id)),
            "notification_announcement_this_round",
        )

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    with engine.connect() as connection:
        wo_status = connection.execute(
            text("SELECT status FROM work_orders WHERE id=:id"),
            {"id": work_order_id},
        ).scalar()
        checks.expect(wo_status == "CLOSED", f"db_work_order_status={wo_status}")
    engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
