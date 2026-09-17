"""Day 4 cross-process HTTP E2E against Nginx. Fake providers only.

Requires DAY4_BASE_URL. Looks up seed identities from TEST_DATABASE_URL.
Path under test: HTTP/Nginx → API → RabbitMQ → Celery Worker → MySQL/Milvus/MinIO.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

BASE = os.environ.get("DAY4_BASE_URL", "").rstrip("/")
WORKER = os.environ.get("DAY4_WORKER_CONTAINER", "property-ai-day4-worker-day4-1")


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
                "('__DEV_PROPERTY_MANAGER__', '__DEV_RESIDENT__', '__DEV_STAFF__')"
            )
        ).all()
        mapping = {str(name): int(user_id) for user_id, name in rows}
        ids["manager"] = mapping["__DEV_PROPERTY_MANAGER__"]
        ids["resident"] = mapping["__DEV_RESIDENT__"]
        ids["staff"] = mapping["__DEV_STAFF__"]
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
        ids["employee_id"] = int(employee) if employee is not None else 0
    engine.dispose()
    return ids


def _token(client: httpx.Client, user_id: int) -> str:
    response = client.post(
        f"{BASE}/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers=_headers(None, f"d4-login-{user_id}"),
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def _wait_status(
    client: httpx.Client,
    token: str,
    work_order_id: int,
    wanted: set[str],
    timeout: float = 90.0,
) -> tuple[str, str]:
    deadline = time.time() + timeout
    last = ""
    request_id = ""
    while time.time() < deadline:
        rid = f"d4-poll-{work_order_id}-{uuid4().hex[:6]}"
        response = client.get(
            f"{BASE}/api/v1/work-orders/{work_order_id}",
            headers=_headers(token, rid),
        )
        if response.status_code == 200:
            payload = response.json().get("data") or {}
            last = str(payload.get("status") or (payload.get("work_order") or {}).get("status") or "")
            request_id = str(response.json().get("request_id") or rid)
            if last in wanted:
                return last, request_id
        time.sleep(1.5)
    raise SystemExit(f"work order {work_order_id} stayed {last}, wanted {wanted}")


def _worker_running() -> bool:
    listed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return WORKER in listed.stdout.splitlines()


def main() -> int:
    if not BASE:
        print("DAY4_BASE_URL is required", file=sys.stderr)
        return 2
    ids = _lookup_ids()
    evidence: list[str] = []
    failed = False
    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{BASE}/healthz", headers=_headers(None, "d4-healthz"))
        ready = client.get(f"{BASE}/api/v1/readyz", headers=_headers(None, "d4-readyz"))
        evidence.append(
            f"healthz={health.status_code} request_id={health.json().get('request_id')}"
        )
        evidence.append(f"readyz={ready.status_code} status={ready.json().get('data', {}).get('status')}")
        deps = ready.json().get("data", {}).get("dependencies", {})
        for name in ("llm", "vlm", "asr", "embedding", "reranker"):
            status = deps.get(name, {}).get("status")
            if status == "up":
                print(f"{name} reported up without a live vendor probe", file=sys.stderr)
                return 1
            evidence.append(f"readyz.{name}={status}")

        public_opsz = client.get(f"{BASE}/api/v1/opsz", headers=_headers(None, "d4-opsz-public"))
        evidence.append(f"opsz_unauthenticated={public_opsz.status_code}")
        if public_opsz.status_code not in {401, 403}:
            failed = True

        manager = _token(client, ids["manager"])
        resident = _token(client, ids["resident"])
        staff = _token(client, ids["staff"])
        opsz = client.get(f"{BASE}/api/v1/opsz", headers=_headers(manager, "d4-opsz"))
        evidence.append(f"opsz_manager={opsz.status_code} queue={opsz.json().get('data', {}).get('celery_queue')}")

        wx = client.post(
            f"{BASE}/api/v1/auth/wechat/login",
            json={"code": f"wx-new-day4-{uuid4().hex[:10]}"},
            headers=_headers(None, "d4-wx-orphan"),
        )
        evidence.append(f"wechat_new={wx.status_code} request_id={wx.json().get('request_id')}")
        if wx.status_code != 200:
            print(wx.text, file=sys.stderr)
            return 1
        orphan = wx.json()["data"]["access_token"]
        bind = client.post(
            f"{BASE}/api/v1/resident-bind-requests",
            json={"house_id": ids["house_id"], "relation_type": "FAMILY"},
            headers=_headers(orphan, "d4-bind"),
        )
        evidence.append(f"bind={bind.status_code}")
        if bind.status_code != 200:
            print(bind.text, file=sys.stderr)
            failed = True
        else:
            bind_id = bind.json()["data"]["id"]
            approved = client.post(
                f"{BASE}/api/v1/admin/resident-bind-requests/{bind_id}/approve",
                headers=_headers(manager, "d4-bind-approve"),
            )
            evidence.append(f"bind_approve={approved.status_code}")
            if approved.status_code == 200:
                resident = orphan

        duplicate_key = f"d4-{uuid4()}"
        created = client.post(
            f"{BASE}/api/v1/work-orders",
            json={"house_id": ids["house_id"], "text": "Kitchen outlet sparking", "category": "ELECTRICAL"},
            headers={**_headers(resident, "d4-wo-create"), "Idempotency-Key": duplicate_key},
        )
        evidence.append(
            f"create_wo={created.status_code} request_id={created.json().get('request_id')}"
        )
        if created.status_code != 200:
            print(created.text, file=sys.stderr)
            print("\n".join(evidence))
            return 1
        work_order_id = created.json()["data"]["id"]
        again = client.post(
            f"{BASE}/api/v1/work-orders",
            json={"house_id": ids["house_id"], "text": "Kitchen outlet sparking", "category": "ELECTRICAL"},
            headers={**_headers(resident, "d4-wo-dup"), "Idempotency-Key": duplicate_key},
        )
        evidence.append(
            f"duplicate_wo={again.status_code} id={again.json().get('data', {}).get('id')} original={work_order_id}"
        )
        if again.status_code != 200 or again.json()["data"]["id"] != work_order_id:
            failed = True

        if _worker_running():
            subprocess.run(["docker", "stop", WORKER], check=False, capture_output=True)
            paused = client.post(
                f"{BASE}/api/v1/work-orders",
                json={"house_id": ids["house_id"], "text": "Worker paused analysis"},
                headers={**_headers(resident, "d4-wo-paused"), "Idempotency-Key": f"d4-pause-{uuid4()}"},
            )
            paused_id = paused.json().get("data", {}).get("id")
            evidence.append(f"paused_create={paused.status_code} id={paused_id}")
            time.sleep(3)
            paused_get = client.get(
                f"{BASE}/api/v1/work-orders/{paused_id}",
                headers=_headers(resident, "d4-wo-paused-get"),
            )
            paused_payload = paused_get.json().get("data") or {}
            paused_status = paused_payload.get("status") or (paused_payload.get("work_order") or {}).get("status")
            evidence.append(f"paused_status={paused_status}")
            subprocess.run(["docker", "start", WORKER], check=False, capture_output=True)
            if paused_id:
                resumed, _rid = _wait_status(
                    client,
                    resident,
                    int(paused_id),
                    {"PENDING_ASSIGNMENT", "ASSIGNED", "PENDING_DISPATCH", "PENDING_ANALYSIS"},
                    timeout=90,
                )
                evidence.append(f"resumed_status={resumed}")
                if resumed == "PENDING_ANALYSIS":
                    resumed, _rid = _wait_status(
                        client,
                        resident,
                        int(paused_id),
                        {"PENDING_ASSIGNMENT", "ASSIGNED", "PENDING_DISPATCH"},
                        timeout=90,
                    )
                    evidence.append(f"resumed_after_worker={resumed}")

        status, _rid = _wait_status(
            client,
            resident,
            work_order_id,
            {"PENDING_ASSIGNMENT", "PENDING_DISPATCH", "ASSIGNED", "PENDING_ANALYSIS"},
        )
        evidence.append(f"wo_status_after_create={status} id={work_order_id}")
        if status == "PENDING_ANALYSIS":
            status, _rid = _wait_status(
                client,
                resident,
                work_order_id,
                {"PENDING_ASSIGNMENT", "ASSIGNED", "PENDING_DISPATCH"},
            )
            evidence.append(f"wo_status_after_worker={status}")

        checkin = client.post(
            f"{BASE}/api/v1/staff/attendance/checkin",
            json={"property_id": ids["property_id"]},
            headers=_headers(staff, "d4-checkin"),
        )
        evidence.append(f"checkin={checkin.status_code}")
        if ids["employee_id"]:
            assigned = client.post(
                f"{BASE}/api/v1/supervisor/work-orders/{work_order_id}/assign",
                json={"employee_id": ids["employee_id"]},
                headers=_headers(manager, "d4-assign"),
            )
            evidence.append(f"assign={assigned.status_code} request_id={assigned.json().get('request_id')}")
            client.post(
                f"{BASE}/api/v1/staff/work-orders/{work_order_id}/accept",
                json={},
                headers=_headers(staff, "d4-accept"),
            )
            client.post(
                f"{BASE}/api/v1/staff/work-orders/{work_order_id}/start",
                json={},
                headers=_headers(staff, "d4-start"),
            )
            receipt = client.post(
                f"{BASE}/api/v1/staff/work-orders/{work_order_id}/receipts",
                json={"problem_type": "ELECTRICAL", "method": "Replace outlet", "resolved": True},
                headers=_headers(staff, "d4-receipt"),
            )
            evidence.append(f"receipt={receipt.status_code}")
            confirm = client.post(
                f"{BASE}/api/v1/work-orders/{work_order_id}/confirm",
                json={},
                headers=_headers(resident, "d4-confirm"),
            )
            evidence.append(f"confirm={confirm.status_code}")

        knowledge = client.post(
            f"{BASE}/api/v1/admin/knowledge-documents",
            json={
                "property_id": ids["property_id"],
                "title": "Day4 装修规定",
                "document_type": "RULE",
                "visibility": "RESIDENT",
                "source_text": "装修仅工作日 08:00-18:00。",
            },
            headers=_headers(manager, "d4-kb"),
        )
        evidence.append(
            f"knowledge={knowledge.status_code} document_id={knowledge.json().get('data', {}).get('document_id')} "
            f"version={knowledge.json().get('data', {}).get('version')}"
        )
        asked = None
        for _ in range(20):
            time.sleep(2)
            asked = client.post(
                f"{BASE}/api/v1/assistant/query",
                json={"question": "装修时间是什么？", "property_id": ids["property_id"]},
                headers=_headers(resident, "d4-ask"),
            )
            if asked.status_code == 200 and asked.json().get("data", {}).get("citations"):
                break
        evidence.append(
            f"assistant={asked.status_code if asked else 'none'} "
            f"status={asked.json().get('data', {}).get('status') if asked else None} "
            f"citations={asked.json().get('data', {}).get('citations') if asked else None}"
        )
        announcement = client.post(
            f"{BASE}/api/v1/admin/announcements",
            json={
                "property_id": ids["property_id"],
                "title": "停水通知",
                "content": "明日上午检修。",
                "audience": "RESIDENT",
            },
            headers=_headers(manager, "d4-ann"),
        )
        evidence.append(f"announcement={announcement.status_code}")
        if announcement.status_code == 200:
            ann_id = announcement.json()["data"]["id"]
            client.post(
                f"{BASE}/api/v1/admin/announcements/{ann_id}/submit-review",
                headers=_headers(manager, "d4-ann-review"),
            )
            published = client.post(
                f"{BASE}/api/v1/admin/announcements/{ann_id}/publish",
                json={"decision": "APPROVE"},
                headers=_headers(manager, "d4-ann-pub"),
            )
            evidence.append(f"publish={published.status_code}")
        notes = client.get(f"{BASE}/api/v1/notifications", headers=_headers(resident, "d4-notes"))
        evidence.append(
            f"notifications={notes.status_code} count={len(notes.json().get('data', {}).get('items', []))}"
        )

        with engine_query() as connection:
            wo_status = connection.execute(
                text("SELECT status FROM work_orders WHERE id=:id"),
                {"id": work_order_id},
            ).scalar()
            evidence.append(f"db_work_order_status={wo_status}")

    print("\n".join(evidence))
    if failed:
        print("DAY4_CROSS_PROCESS_E2E=FAIL (Fake providers)")
        return 1
    print("DAY4_CROSS_PROCESS_E2E=PASS (Fake providers; not live vendor)")
    return 0


def engine_query():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(url)
    return engine.connect()


if __name__ == "__main__":
    raise SystemExit(main())
