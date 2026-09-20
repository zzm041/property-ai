"""Close existing synthetic incident orders through APIs and prove charge transfer."""

from __future__ import annotations

import json
import re
import subprocess
import sys
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
OUTPUT = Path(__file__).with_name("fix1-lifecycle-evidence.json")
WORK_ORDER_IDS = [15, 16, 17, 18, 19, 20]


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
                    "SELECT id, status, current_assignment_id FROM work_orders "
                    "WHERE id BETWEEN 15 AND 20 ORDER BY id"
                )
            ).mappings()
        ]
        owners = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, work_order_id, status, reason, "
                    "JSON_UNQUOTE(JSON_EXTRACT(payload_json, "
                    "'$.incident_capacity_owner')) AS capacity_owner "
                    "FROM work_order_assignments WHERE work_order_id BETWEEN 15 AND 20 "
                    "ORDER BY id"
                )
            ).mappings()
        ]
    return {
        "incident": incident,
        "daily": daily,
        "orders": orders,
        "assignment_capacity_owners": owners,
    }


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=False)
    engine = create_engine(settings.test_database_url)
    timeline: list[dict[str, object]] = []
    try:
        with httpx.Client(timeout=10) as client:
            resident_token, _ = _login(client, private["phones"]["resident"])
            manager_token, _ = _login(
                client,
                private["phones"]["tenant_admin"],
            )
            primary_token, primary_user_id = _login(
                client,
                private["phones"]["primary"],
            )
            secondary_token, secondary_user_id = _login(
                client,
                private["phones"]["full"],
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
                headers=_auth(manager_token, "incident-fix1-roster"),
            )["items"]
            staff_tokens = {
                _employee_id_by_user(roster, primary_user_id): primary_token,
                _employee_id_by_user(roster, secondary_user_id): secondary_token,
            }
            staff_token = staff_tokens[7]
            before = _state(engine, incident_id=1, employee_id=7)
            for work_order_id in WORK_ORDER_IDS:
                accepted = _request(
                    client,
                    "POST",
                    f"/staff/work-orders/{work_order_id}/accept",
                    json={},
                    headers=_auth(
                        staff_token,
                        f"incident-fix1-accept-{work_order_id}",
                    ),
                )
                started = _request(
                    client,
                    "POST",
                    f"/staff/work-orders/{work_order_id}/start",
                    json={"version": int(accepted["version"])},
                    headers=_auth(
                        staff_token,
                        f"incident-fix1-start-{work_order_id}",
                    ),
                )
                receipted = _request(
                    client,
                    "POST",
                    f"/staff/work-orders/{work_order_id}/receipts",
                    json={
                        "problem_type": "ELECTRICAL",
                        "method": "Shared incident electrical inspection",
                        "resolved": True,
                        "version": int(started["version"]),
                    },
                    headers=_auth(
                        staff_token,
                        f"incident-fix1-receipt-{work_order_id}",
                    ),
                )
                closed = _request(
                    client,
                    "POST",
                    f"/work-orders/{work_order_id}/confirm",
                    json={"version": int(receipted["version"])},
                    headers=_auth(
                        resident_token,
                        f"incident-fix1-confirm-{work_order_id}",
                    ),
                )
                if closed["status"] != "CLOSED":
                    raise RuntimeError(f"work order {work_order_id} did not close")
                timeline.append(
                    {
                        "closed_work_order_id": work_order_id,
                        "state": _state(
                            engine,
                            incident_id=1,
                            employee_id=7,
                        ),
                    }
                )
        after = _state(engine, incident_id=1, employee_id=7)
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()

    observed_loads = [int(item["state"]["daily"]["current_load"]) for item in timeline]
    observed_primaries = [
        item["state"]["incident"]["primary_work_order_id"] for item in timeline
    ]
    evidence = {
        "task_id": "INCIDENT-AGGREGATION-005-FIX-1",
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "queue": "property_ai.auth001",
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
        },
        "reused_existing_work_order_ids": WORK_ORDER_IDS,
        "before": before,
        "timeline": timeline,
        "after": after,
        "assertions": {
            "all_mutations_used_authenticated_business_apis": True,
            "primary_charge_transferred_on_order_15": observed_loads[0]
            == int(before["daily"]["current_load"])
            and observed_primaries[0] == 19,
            "independent_prethreshold_charges_released": observed_loads[1:4]
            == [4, 3, 2],
            "charge_transferred_from_19_to_20": observed_loads[4] == 2
            and observed_primaries[4] == 20,
            "last_owner_released_once": observed_loads[5] == 1
            and observed_primaries[5] is None,
            "all_six_orders_closed": all(
                item["status"] == "CLOSED" for item in after["orders"]
            ),
            "no_active_assignment_left": all(
                item["current_assignment_id"] is None for item in after["orders"]
            ),
        },
    }
    if not all(evidence["assertions"].values()):
        raise RuntimeError(f"FIX-1 runtime assertions failed: {evidence['assertions']}")
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
