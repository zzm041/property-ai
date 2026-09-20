"""AUTH001 runtime proof for common-incident shared automatic dispatch.

All mutations use public business APIs. Database access is read-only evidence.
Phones, OTPs, tokens, resident identity, and names are never written to output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import bindparam, create_engine, text

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for path in (BACKEND, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auth001_isolated_runtime import build_isolated_settings  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"
CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
OUTPUT = Path(__file__).with_name("runtime-evidence.json")


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


def _employee(items: list[dict], user_id: int) -> dict:
    for item in items:
        employee = item.get("employee", item)
        if int(employee.get("user_id") or 0) == user_id:
            return employee
    raise RuntimeError("authenticated employee is not in the manager roster")


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _wait_assigned(engine, work_order_id: int) -> dict[str, object]:
    deadline = time.monotonic() + 45
    statement = text(
        "SELECT wo.status, wo.incident_id, wo.current_assignment_id, "
        "wa.employee_id, wa.assigned_source, wa.reason, wa.payload_json "
        "FROM work_orders wo LEFT JOIN work_order_assignments wa "
        "ON wa.id = wo.current_assignment_id WHERE wo.id = :work_order_id"
    )
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = connection.execute(
                statement,
                {"work_order_id": work_order_id},
            ).mappings().one()
        if row["status"] == "ASSIGNED" and row["current_assignment_id"] is not None:
            result = dict(row)
            result["payload_json"] = _json_object(result["payload_json"])
            return result
        time.sleep(0.25)
    raise RuntimeError(f"work order {work_order_id} did not reach ASSIGNED")


def _daily_load(engine, *, property_id: int, employee_id: int) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT business_date, on_duty, current_load "
                "FROM employee_daily_statuses "
                "WHERE property_id = :property_id AND employee_id = :employee_id "
                "ORDER BY business_date DESC LIMIT 1"
            ),
            {"property_id": property_id, "employee_id": employee_id},
        ).mappings().one()
    return dict(row)


def _create_order(
    client: httpx.Client,
    *,
    resident_token: str,
    house_id: int,
    suffix: str,
    number: int,
) -> int:
    created = _request(
        client,
        "POST",
        "/work-orders",
        json={
            "house_id": house_id,
            "text": f"Common electrical incident synthetic probe {suffix} item {number}",
            "category": "ELECTRICAL",
            "priority": "HIGH",
            "severity": "HIGH",
        },
        headers={
            **_auth(resident_token, f"incident-create-{suffix}-{number}"),
            "Idempotency-Key": f"incident-aggregation-005-{suffix}-{number}",
        },
    )
    return int(created["id"])


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=True)
    engine = create_engine(settings.test_database_url)
    suffix = uuid4().hex[:10]
    org = private["org"]
    original_capacities: dict[int, int] = {}
    restored: dict[int, bool] = {}
    manager_token = ""
    evidence: dict[str, object] | None = None

    try:
        with httpx.Client(timeout=10) as client:
            manager_token, _ = _login(client, private["phones"]["tenant_admin"])
            resident_token, _ = _login(client, private["phones"]["resident"])
            _, primary_user_id = _login(client, private["phones"]["primary"])
            _, secondary_user_id = _login(client, private["phones"]["full"])
            roster = _request(
                client,
                "GET",
                "/admin/employees",
                params={
                    "tenant_id": int(org["tenant_id"]),
                    "property_id": int(org["property_id"]),
                    "include_historical": "true",
                    "limit": 100,
                },
                headers=_auth(manager_token, f"incident-roster-{suffix}"),
            )["items"]
            test_employees = (
                _employee(roster, primary_user_id),
                _employee(roster, secondary_user_id),
            )
            for employee in test_employees:
                employee_id = int(employee["id"])
                original_capacities[employee_id] = int(employee["max_capacity"])
                _request(
                    client,
                    "PATCH",
                    f"/admin/employees/{employee_id}",
                    json={"max_capacity": 100},
                    headers=_auth(
                        manager_token,
                        f"incident-capacity-open-{suffix}-{employee_id}",
                    ),
                )

            created_ids: list[int] = []
            assignments: list[dict[str, object]] = []
            for number in range(1, 6):
                work_order_id = _create_order(
                    client,
                    resident_token=resident_token,
                    house_id=int(org["house_id"]),
                    suffix=suffix,
                    number=number,
                )
                created_ids.append(work_order_id)
                assignments.append(_wait_assigned(engine, work_order_id))

            fifth = assignments[-1]
            incident_id = int(fifth["incident_id"] or 0)
            if incident_id <= 0:
                raise RuntimeError("threshold order did not join an incident")
            with engine.connect() as connection:
                incident = dict(
                    connection.execute(
                        text(
                            "SELECT id, tenant_id, property_id, building_id, status, "
                            "category, subcategory, primary_work_order_id, "
                            "primary_employee_id, work_order_count, first_seen_at, last_seen_at "
                            "FROM incidents WHERE id = :incident_id"
                        ),
                        {"incident_id": incident_id},
                    ).mappings().one()
                )
            primary_employee_id = int(incident["primary_employee_id"] or 0)
            if primary_employee_id <= 0:
                raise RuntimeError("incident did not retain a primary employee")
            if int(fifth["employee_id"]) != primary_employee_id:
                raise RuntimeError("threshold order did not follow the primary employee")
            fifth_payload = fifth["payload_json"]
            if fifth_payload.get("incident_follow") is not True:
                raise RuntimeError("threshold assignment lacks incident-follow provenance")

            load_at_threshold = _daily_load(
                engine,
                property_id=int(org["property_id"]),
                employee_id=primary_employee_id,
            )
            full_capacity = int(load_at_threshold["current_load"])
            if full_capacity < 1:
                raise RuntimeError("primary employee has no capacity load to share")
            _request(
                client,
                "PATCH",
                f"/admin/employees/{primary_employee_id}",
                json={"max_capacity": full_capacity},
                headers=_auth(
                    manager_token,
                    f"incident-capacity-full-{suffix}-{primary_employee_id}",
                ),
            )

            sixth_id = _create_order(
                client,
                resident_token=resident_token,
                house_id=int(org["house_id"]),
                suffix=suffix,
                number=6,
            )
            created_ids.append(sixth_id)
            sixth = _wait_assigned(engine, sixth_id)
            assignments.append(sixth)
            load_after_follow = _daily_load(
                engine,
                property_id=int(org["property_id"]),
                employee_id=primary_employee_id,
            )
            if int(sixth["incident_id"] or 0) != incident_id:
                raise RuntimeError("subsequent order did not reuse the incident")
            if int(sixth["employee_id"]) != primary_employee_id:
                raise RuntimeError("full primary employee was not reused")
            sixth_payload = sixth["payload_json"]
            if sixth_payload.get("incident_follow") is not True:
                raise RuntimeError("subsequent assignment lacks incident-follow provenance")
            if int(load_after_follow["current_load"]) != full_capacity:
                raise RuntimeError("incident follower duplicated primary capacity load")

            deadline = time.monotonic() + 20
            delivery_states: list[str] = []
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    delivery_states = list(
                        connection.execute(
                            text(
                                "SELECT nd.status FROM notification_deliveries nd "
                                "JOIN notifications n ON n.id = nd.notification_id "
                                "WHERE n.resource_type = 'WORK_ORDER' "
                                "AND n.resource_id = :work_order_id ORDER BY nd.id"
                            ),
                            {"work_order_id": sixth_id},
                        ).scalars()
                    )
                if delivery_states and all(state != "PENDING" for state in delivery_states):
                    break
                time.sleep(0.25)

            with engine.connect() as connection:
                final_incident = dict(
                    connection.execute(
                        text(
                            "SELECT id, status, category, subcategory, "
                            "primary_work_order_id, primary_employee_id, "
                            "work_order_count, first_seen_at, last_seen_at "
                            "FROM incidents WHERE id = :incident_id"
                        ),
                        {"incident_id": incident_id},
                    ).mappings().one()
                )
                links = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            "SELECT work_order_id FROM incident_work_orders "
                            "WHERE incident_id = :incident_id ORDER BY work_order_id"
                        ),
                        {"incident_id": incident_id},
                    ).mappings()
                ]
                analyses = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            "SELECT work_order_id, status, category, subcategory, "
                            "prompt_version FROM work_order_analyses "
                            "WHERE work_order_id IN :work_order_ids ORDER BY work_order_id"
                        ).bindparams(bindparam("work_order_ids", expanding=True)),
                        {"work_order_ids": created_ids},
                    ).mappings()
                ]
                link_audit_count = int(
                    connection.execute(
                        text(
                            "SELECT COUNT(*) FROM audit_logs "
                            "WHERE action = 'WORK_ORDER_LINKED_TO_INCIDENT' "
                            "AND resource_type = 'INCIDENT' AND resource_id = :incident_id"
                        ),
                        {"incident_id": incident_id},
                    ).scalar_one()
                )
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()

            evidence = {
                "task_id": "INCIDENT-AGGREGATION-005",
                "environment": {
                    "api": API,
                    "database": "127.0.0.1:3307/property_ai_auth001_test",
                    "revision": revision,
                    "queue": settings.celery_task_queue,
                    "commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"],
                        cwd=REPO,
                        text=True,
                    ).strip(),
                    "model_mode": "development_fake",
                },
                "created_work_order_ids": created_ids,
                "incident": final_incident,
                "links": links,
                "analyses": analyses,
                "assignments": [
                    {
                        "work_order_id": work_order_id,
                        "assignment_id": int(item["current_assignment_id"]),
                        "employee_id": int(item["employee_id"]),
                        "assigned_source": item["assigned_source"],
                        "reason": item["reason"],
                        "incident_id": item["incident_id"],
                        "incident_follow": item["payload_json"].get(
                            "incident_follow"
                        ),
                    }
                    for work_order_id, item in zip(created_ids, assignments, strict=True)
                ],
                "capacity_proof": {
                    "primary_employee_id": primary_employee_id,
                    "max_capacity_during_sixth": full_capacity,
                    "load_before_sixth": int(load_at_threshold["current_load"]),
                    "load_after_sixth": int(load_after_follow["current_load"]),
                    "sixth_followed_primary": True,
                },
                "audit": {"incident_link_actions": link_audit_count},
                "sixth_notification_delivery_states": delivery_states,
                "assertions": {
                    "one_incident_for_six_orders": len(links) == 6,
                    "same_subcategory": all(
                        item["subcategory"] == "outlet_spark" for item in analyses
                    ),
                    "threshold_and_later_follow_primary": all(
                        int(item["employee_id"]) == primary_employee_id
                        and item["payload_json"].get("incident_follow") is True
                        for item in assignments[4:]
                    ),
                    "full_primary_reused": int(sixth["employee_id"])
                    == primary_employee_id,
                    "shared_load_not_duplicated": int(
                        load_after_follow["current_load"]
                    )
                    == full_capacity,
                    "business_api_only_mutations": True,
                    "no_real_model_claim": True,
                },
            }
    finally:
        if manager_token and original_capacities:
            with httpx.Client(timeout=10) as client:
                for employee_id, original in original_capacities.items():
                    try:
                        _request(
                            client,
                            "PATCH",
                            f"/admin/employees/{employee_id}",
                            json={"max_capacity": original},
                            headers=_auth(
                                manager_token,
                                f"incident-capacity-restore-{suffix}-{employee_id}",
                            ),
                        )
                        restored[employee_id] = True
                    except Exception:
                        restored[employee_id] = False
        engine.dispose()

    if evidence is None:
        raise RuntimeError("runtime evidence was not completed")
    evidence["capacity_restored"] = restored
    if not all(restored.values()):
        raise RuntimeError("test employee capacities were not restored")
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
