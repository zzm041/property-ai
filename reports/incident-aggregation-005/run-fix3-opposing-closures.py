"""Force the primary/follower lock cycle and prove stable conflict recovery."""

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
OUTPUT = Path(__file__).with_name("fix3-opposing-closures-evidence.json")
WORK_ORDER_IDS = [33, 34, 35, 36, 37, 38]


def _request(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, f"{API}{path}", **kwargs)
    response.raise_for_status()
    return response.json()["data"]


def _login(client: httpx.Client, phone: str) -> tuple[str, int]:
    challenge = _request(
        client, "POST", "/auth/challenge", json={"phone": phone, "purpose": "login"}
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
        headers=_auth(staff_token, f"incident-fix3-accept-{work_order_id}"),
    )
    started = _request(
        client,
        "POST",
        f"/staff/work-orders/{work_order_id}/start",
        json={"version": int(accepted["version"])},
        headers=_auth(staff_token, f"incident-fix3-start-{work_order_id}"),
    )
    return _request(
        client,
        "POST",
        f"/staff/work-orders/{work_order_id}/receipts",
        json={
            "problem_type": "ELECTRICAL",
            "method": "Opposing closure concurrency inspection",
            "resolved": True,
            "version": int(started["version"]),
        },
        headers=_auth(staff_token, f"incident-fix3-receipt-{work_order_id}"),
    )


def _confirm_raw(
    token: str,
    work_order_id: int,
    version: int,
    request_id: str,
) -> dict[str, object]:
    with httpx.Client(timeout=40) as client:
        response = client.post(
            f"{API}/work-orders/{work_order_id}/confirm",
            json={"version": version},
            headers=_auth(token, request_id),
        )
    return {
        "work_order_id": work_order_id,
        "status_code": response.status_code,
        "body": response.json(),
    }


def _state(engine, *, incident_id: int, employee_id: int) -> dict[str, object]:
    ids = ",".join(str(item) for item in WORK_ORDER_IDS)
    with engine.connect() as connection:
        incident = dict(
            connection.execute(
                text(
                    "SELECT primary_work_order_id, primary_employee_id, work_order_count "
                    "FROM incidents WHERE id = :incident_id"
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
        closed_logs = {
            int(row["work_order_id"]): int(row["count"])
            for row in connection.execute(
                text(
                    "SELECT work_order_id, COUNT(*) AS count FROM work_order_logs "
                    f"WHERE work_order_id IN ({ids}) AND to_status = 'CLOSED' "
                    "GROUP BY work_order_id"
                )
            ).mappings()
        }
        confirmations = {
            int(row["work_order_id"]): int(row["count"])
            for row in connection.execute(
                text(
                    "SELECT work_order_id, COUNT(*) AS count FROM work_order_feedbacks "
                    f"WHERE work_order_id IN ({ids}) AND confirmed = 1 "
                    "GROUP BY work_order_id"
                )
            ).mappings()
        }
    return {
        "incident": incident,
        "daily": daily,
        "orders": orders,
        "assignments": assignments,
        "closed_log_counts": closed_logs,
        "confirmation_counts": confirmations,
    }


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=False)
    engine = create_engine(settings.test_database_url)
    incident_id = 4
    primary_id = 33
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
                headers=_auth(manager_token, "incident-fix3-roster"),
            )["items"]
            staff_tokens = {
                _employee_id_by_user(roster, primary_user_id): primary_token,
                _employee_id_by_user(roster, secondary_user_id): secondary_token,
            }
            staff_token = staff_tokens[7]
            before = _state(engine, incident_id=incident_id, employee_id=7)
            follower_id = next(
                int(item["work_order_id"])
                for item in before["assignments"]
                if item["status"] == "ACTIVE"
                and str(item["incident_follow"]).lower() == "true"
            )
            receipts = {
                work_order_id: _prepare_receipt(
                    client,
                    staff_token=staff_token,
                    work_order_id=work_order_id,
                )
                for work_order_id in (primary_id, follower_id)
            }

            with engine.connect() as locker:
                transaction = locker.begin()
                locker.execute(
                    text("SELECT id FROM incidents WHERE id = :id FOR UPDATE"),
                    {"id": incident_id},
                ).scalar_one()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    primary_future = pool.submit(
                        _confirm_raw,
                        resident_token,
                        primary_id,
                        int(receipts[primary_id]["version"]),
                        "incident-fix3-confirm-primary",
                    )
                    time.sleep(1.0)
                    primary_waited = not primary_future.done()
                    follower_future = pool.submit(
                        _confirm_raw,
                        resident_token,
                        follower_id,
                        int(receipts[follower_id]["version"]),
                        "incident-fix3-confirm-follower",
                    )
                    time.sleep(1.0)
                    follower_waited = not follower_future.done()
                    transaction.rollback()
                    opposing_results = [
                        primary_future.result(timeout=40),
                        follower_future.result(timeout=40),
                    ]

            successes = [item for item in opposing_results if item["status_code"] == 200]
            conflicts = [item for item in opposing_results if item["status_code"] == 409]
            if len(successes) != 1 or len(conflicts) != 1:
                raise RuntimeError(f"expected one success and one conflict: {opposing_results}")
            conflict = conflicts[0]
            error = conflict["body"].get("error", {})
            if error.get("code") != "WO.CONFLICT" or not error.get("details", {}).get(
                "retryable"
            ):
                raise RuntimeError(f"conflict was not stable/retryable: {conflict}")
            conflict_id = int(conflict["work_order_id"])
            retry_result = _confirm_raw(
                resident_token,
                conflict_id,
                int(receipts[conflict_id]["version"]),
                "incident-fix3-confirm-retry",
            )
            if retry_result["status_code"] != 200:
                raise RuntimeError(f"conflicted closure retry failed: {retry_result}")

            for work_order_id in WORK_ORDER_IDS:
                if work_order_id in {primary_id, follower_id}:
                    continue
                receipt = _prepare_receipt(
                    client,
                    staff_token=staff_token,
                    work_order_id=work_order_id,
                )
                result = _confirm_raw(
                    resident_token,
                    work_order_id,
                    int(receipt["version"]),
                    f"incident-fix3-confirm-{work_order_id}",
                )
                if result["status_code"] != 200:
                    raise RuntimeError(f"closure failed for {work_order_id}: {result}")

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
        "task_id": "INCIDENT-AGGREGATION-005-FIX-3",
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
        "work_order_ids": WORK_ORDER_IDS,
        "opposing_lock_setup": {
            "external_incident_lock_only_no_state_mutation": True,
            "primary_work_order_id": primary_id,
            "follower_work_order_id": follower_id,
            "primary_waited_for_incident": primary_waited,
            "follower_waited_for_incident": follower_waited,
        },
        "opposing_results": opposing_results,
        "retry_result": retry_result,
        "before": before,
        "after": after,
        "assertions": {
            "all_state_mutations_used_authenticated_business_apis": True,
            "both_requests_waited_before_lock_release": primary_waited
            and follower_waited,
            "one_success_one_retryable_conflict": len(successes) == 1
            and len(conflicts) == 1,
            "conflicted_request_retry_succeeded": retry_result["status_code"] == 200,
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
            "one_closed_log_per_order": all(
                after["closed_log_counts"].get(item) == 1 for item in WORK_ORDER_IDS
            ),
            "one_confirmation_per_order": all(
                after["confirmation_counts"].get(item) == 1 for item in WORK_ORDER_IDS
            ),
            "no_real_model_claim": True,
        },
    }
    if not all(evidence["assertions"].values()):
        raise RuntimeError(f"FIX-3 assertions failed: {evidence['assertions']}")
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
