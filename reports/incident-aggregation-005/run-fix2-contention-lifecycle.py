"""Close the existing incident through APIs while one follower row is locked."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for path in (BACKEND, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"
CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
OUTPUT = Path(__file__).with_name("fix2-contention-lifecycle-evidence.json")
WORK_ORDER_IDS = [27, 28, 29, 30, 31, 32]


def _request(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, f"{API}{path}", **kwargs)
    response.raise_for_status()
    return response.json()["data"]


def _login(client: httpx.Client, phone: str) -> tuple[str, int]:
    challenge = _request(
        client,
        "POST",
        "/auth/challenge",
        json={"phone": phone, "purpose": "login"},
    )
    numbers = [int(item) for item in re.findall(r"\d+", challenge["prompt"])]
    if len(numbers) != 2:
        raise RuntimeError("visual challenge prompt was not understood")
    _request(
        client,
        "POST",
        "/auth/sms/send",
        json={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": str(sum(numbers)),
        },
    )
    inbox = _request(
        client,
        "POST",
        "/auth/sms/test-inbox",
        json={"phone": phone, "purpose": "login"},
    )
    issued = _request(
        client,
        "POST",
        "/auth/sms/login",
        json={"phone": phone, "code": inbox["code"]},
    )
    return str(issued["access_token"]), int(issued["user_id"])


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def _employee_id_by_user(items: list[dict], user_id: int) -> int:
    for item in items:
        employee = item.get("employee", item)
        if int(employee.get("user_id") or 0) == user_id:
            return int(employee["id"])
    raise RuntimeError("authenticated employee is not in the manager roster")


def _state(engine, *, incident_id: int, employee_id: int) -> dict[str, object]:
    ids = ",".join(str(item) for item in WORK_ORDER_IDS)
    with engine.connect() as connection:
        incident = dict(
            connection.execute(
                text(
                    "SELECT primary_work_order_id, primary_employee_id, "
                    "work_order_count FROM incidents WHERE id = :incident_id"
                ),
                {"incident_id": incident_id},
            ).mappings().one()
        )
        daily = dict(
            connection.execute(
                text(
                    "SELECT business_date, current_load FROM employee_daily_statuses "
                    "WHERE employee_id = :employee_id "
                    "ORDER BY business_date DESC LIMIT 1"
                ),
                {"employee_id": employee_id},
            ).mappings().one()
        )
        orders = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, status, version, current_assignment_id FROM work_orders "
                    f"WHERE id IN ({ids}) ORDER BY id"
                )
            ).mappings()
        ]
        assignments = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, work_order_id, employee_id, status, reason, "
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.incident_follow')) "
                    "AS incident_follow, "
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
                    "'$.incident_capacity_owner')) AS capacity_owner "
                    "FROM work_order_assignments "
                    f"WHERE work_order_id IN ({ids}) ORDER BY id"
                )
            ).mappings()
        ]
    return {
        "incident": incident,
        "daily": daily,
        "orders": orders,
        "assignments": assignments,
    }


def _prepare_receipt(
    client: httpx.Client,
    *,
    staff_token: str,
    work_order_id: int,
) -> dict:
    accepted = _request(
        client,
        "POST",
        f"/staff/work-orders/{work_order_id}/accept",
        json={},
        headers=_auth(staff_token, f"incident-fix2-accept-{work_order_id}"),
    )
    started = _request(
        client,
        "POST",
        f"/staff/work-orders/{work_order_id}/start",
        json={"version": int(accepted["version"])},
        headers=_auth(staff_token, f"incident-fix2-start-{work_order_id}"),
    )
    return _request(
        client,
        "POST",
        f"/staff/work-orders/{work_order_id}/receipts",
        json={
            "problem_type": "ELECTRICAL",
            "method": "Contended shared incident inspection",
            "resolved": True,
            "version": int(started["version"]),
        },
        headers=_auth(staff_token, f"incident-fix2-receipt-{work_order_id}"),
    )


def _confirm(token: str, work_order_id: int, version: int) -> dict:
    with httpx.Client(timeout=30) as client:
        return _request(
            client,
            "POST",
            f"/work-orders/{work_order_id}/confirm",
            json={"version": version},
            headers=_auth(token, f"incident-fix2-confirm-{work_order_id}"),
        )


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=False)
    engine = create_engine(settings.test_database_url)
    incident_id = 3
    primary_id = 27
    close_order = [28, 29, 30, 31, 32]
    timeline: list[dict[str, object]] = []
    try:
        with httpx.Client(timeout=15) as client:
            resident_token, _ = _login(client, private["phones"]["resident"])
            manager_token, _ = _login(client, private["phones"]["tenant_admin"])
            primary_token, primary_user_id = _login(
                client, private["phones"]["primary"]
            )
            secondary_token, secondary_user_id = _login(
                client, private["phones"]["full"]
            )
            roster = _request(
                client,
                "GET",
                "/admin/employees",
                params={
                    "tenant_id": int(private["org"]["tenant_id"]),
                    "property_id": int(private["org"]["property_id"]),
                    "include_historical": "true",
                    "limit": 100,
                },
                headers=_auth(manager_token, "incident-fix2-roster"),
            )["items"]
            staff_tokens = {
                _employee_id_by_user(roster, primary_user_id): primary_token,
                _employee_id_by_user(roster, secondary_user_id): secondary_token,
            }
            staff_token = staff_tokens[7]
            before = _state(engine, incident_id=incident_id, employee_id=7)
            primary_assignment_id = next(
                int(item["id"])
                for item in before["assignments"]
                if int(item["work_order_id"]) == primary_id
                and item["status"] == "ACTIVE"
            )
            locked_follower = next(
                item
                for item in before["assignments"]
                if int(item["work_order_id"]) != primary_id
                and item["status"] == "ACTIVE"
                and int(item["employee_id"]) == 7
                and str(item["incident_follow"]).lower() == "true"
            )
            active_follower_ids = {
                int(item["work_order_id"])
                for item in before["assignments"]
                if int(item["work_order_id"]) != primary_id
                and item["status"] == "ACTIVE"
                and int(item["employee_id"]) == 7
            }
            receipt = _prepare_receipt(
                client,
                staff_token=staff_token,
                work_order_id=primary_id,
            )
            with engine.connect() as locker:
                transaction = locker.begin()
                locker.execute(
                    text(
                        "SELECT id FROM work_order_assignments "
                        "WHERE id = :assignment_id FOR UPDATE"
                    ),
                    {"assignment_id": int(locked_follower["id"])},
                ).scalar_one()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        _confirm,
                        resident_token,
                        primary_id,
                        int(receipt["version"]),
                    )
                    time.sleep(1.0)
                    blocked_before_release = not future.done()
                    transaction.rollback()
                    closed = future.result(timeout=30)
            if closed["status"] != "CLOSED":
                raise RuntimeError("contended primary did not close")
            after_primary = _state(engine, incident_id=incident_id, employee_id=7)
            timeline.append(
                {"closed_work_order_id": primary_id, "state": after_primary}
            )
            for work_order_id in close_order:
                receipt = _prepare_receipt(
                    client,
                    staff_token=staff_token,
                    work_order_id=work_order_id,
                )
                closed = _confirm(
                    resident_token,
                    work_order_id,
                    int(receipt["version"]),
                )
                if closed["status"] != "CLOSED":
                    raise RuntimeError(f"work order {work_order_id} did not close")
                timeline.append(
                    {
                        "closed_work_order_id": work_order_id,
                        "state": _state(engine, incident_id=incident_id, employee_id=7),
                    }
                )
        after = _state(engine, incident_id=incident_id, employee_id=7)
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()

    initial_charges = sum(
        1
        for item in before["assignments"]
        if item["status"] == "ACTIVE"
        and str(item["incident_follow"]).lower() != "true"
    )
    evidence = {
        "task_id": "INCIDENT-AGGREGATION-005-FIX-2-CONTENTION",
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "queue": "property_ai.auth001",
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "model_mode": "development_fake",
        },
        "reused_existing_work_order_ids": WORK_ORDER_IDS,
        "contention": {
            "locked_follower_work_order_id": int(locked_follower["work_order_id"]),
            "locked_follower_assignment_id": int(locked_follower["id"]),
            "ending_primary_work_order_id": primary_id,
            "ending_primary_assignment_id": primary_assignment_id,
            "confirm_blocked_before_lock_release": blocked_before_release,
            "lock_only_no_direct_state_mutation": True,
        },
        "before": before,
        "timeline": timeline,
        "after": after,
        "assertions": {
            "all_state_mutations_used_authenticated_business_apis": True,
            "confirm_waited_for_locked_follower": blocked_before_release,
            "capacity_preserved_after_contended_primary_close": (
                int(after_primary["daily"]["current_load"])
                == int(before["daily"]["current_load"])
            ),
            "active_follower_promoted": (
                int(after_primary["incident"]["primary_work_order_id"])
                in active_follower_ids
            ),
            "all_six_orders_closed": all(
                item["status"] == "CLOSED" for item in after["orders"]
            ),
            "no_active_assignment_left": all(
                item["current_assignment_id"] is None for item in after["orders"]
            ),
            "all_initial_charges_released_once": (
                int(after["daily"]["current_load"])
                == int(before["daily"]["current_load"]) - initial_charges
            ),
            "incident_primary_cleared": (
                after["incident"]["primary_work_order_id"] is None
                and after["incident"]["primary_employee_id"] is None
            ),
            "no_real_model_claim": True,
        },
    }
    if not all(evidence["assertions"].values()):
        raise RuntimeError(f"FIX-2 contention assertions failed: {evidence['assertions']}")
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
