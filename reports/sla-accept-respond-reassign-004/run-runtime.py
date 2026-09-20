"""Targeted AUTH001 runtime proof for assignment-phase SLA behavior.

All state changes use public business APIs or the production scheduler service.
The controlled clock is scoped to one newly created synthetic work order. Secrets,
phone numbers, OTPs, tokens, and resident identity are never written to evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for path in (BACKEND, OPS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.core.database import create_database_engine
from app.core.time import utc_now
from app.model.ai import Notification
from app.model.employee import EmployeeDailyStatus
from app.model.knowledge import NotificationDelivery
from app.model.work_order import SlaClock, WorkOrder, WorkOrderAssignment, WorkOrderLog
from app.service.sla import SlaService
from auth001_isolated_runtime import apply_isolated_environ, build_isolated_settings

API = "http://127.0.0.1:8083/api/v1"
CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
OUTPUT = Path(__file__).with_name("runtime-evidence.json")
PHASE_TITLES = ("Work order accept timeout", "Work order response timeout")


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
    raise RuntimeError("authenticated employee is not in the scoped manager roster")


def _active_assignment(session: Session, work_order_id: int):
    return session.scalar(
        select(WorkOrderAssignment).where(
            WorkOrderAssignment.work_order_id == work_order_id,
            WorkOrderAssignment.status == "ACTIVE",
            WorkOrderAssignment.ended_at.is_(None),
        )
    )


def _wait_assignment(engine, work_order_id: int, *, exclude_id: int | None = None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            record = session.get(WorkOrder, work_order_id)
            assignment = _active_assignment(session, work_order_id)
            if (
                record is not None
                and record.status == "ASSIGNED"
                and assignment is not None
                and assignment.id != exclude_id
            ):
                result = {
                    "work_order_version": record.version,
                    "assignment_id": assignment.id,
                    "employee_id": assignment.employee_id,
                    "assigned_at": assignment.assigned_at,
                }
                return result
        time.sleep(0.25)
    raise RuntimeError("automatic assignment did not reach ASSIGNED in time")


def _load_clock(engine, work_order_id: int) -> dict[str, datetime | int]:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        clock = session.scalar(
            select(SlaClock).where(SlaClock.work_order_id == work_order_id)
        )
        if clock is None:
            raise RuntimeError("work order has no SLA clock")
        return {
            "id": clock.id,
            "accept_due_at": clock.accept_due_at,
            "respond_due_at": clock.respond_due_at,
        }


def _sweep_assignment(engine, settings, work_order_id: int, at: datetime):
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        return SlaService(session, settings=settings).sweep_assignment_deadlines(
            now=at,
            work_order_id=work_order_id,
        ).as_dict()


def _wait_phase_deliveries(engine, work_order_id: int, expected_minimum: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            states = session.scalars(
                select(NotificationDelivery.status)
                .join(Notification, Notification.id == NotificationDelivery.notification_id)
                .where(
                    Notification.resource_type == "WORK_ORDER",
                    Notification.resource_id == work_order_id,
                    Notification.title.in_(PHASE_TITLES),
                )
            ).all()
        if len(states) >= expected_minimum and all(state != "PENDING" for state in states):
            return
        time.sleep(0.25)
    raise RuntimeError("assignment SLA notification deliveries did not settle")


def _iso(value):
    return None if value is None else value.isoformat()


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=True)
    apply_isolated_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    suffix = uuid4().hex[:10]

    try:
        with httpx.Client(timeout=10) as client:
            manager_token, _ = _login(client, private["phones"]["tenant_admin"])
            resident_token, _ = _login(client, private["phones"]["resident"])
            primary_token, primary_user_id = _login(
                client, private["phones"]["primary"]
            )
            full_token, full_user_id = _login(client, private["phones"]["full"])
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
                headers=_auth(manager_token, f"sla-roster-{suffix}"),
            )["items"]
            primary_employee_id = _employee_id_by_user(roster, primary_user_id)
            full_employee_id = _employee_id_by_user(roster, full_user_id)
            employee_tokens = {
                primary_employee_id: primary_token,
                full_employee_id: full_token,
            }
            for employee_id in sorted(employee_tokens):
                _request(
                    client,
                    "PATCH",
                    f"/admin/employees/{employee_id}",
                    json={"max_capacity": 100},
                    headers=_auth(
                        manager_token,
                        f"sla-capacity-{suffix}-{employee_id}",
                    ),
                )

            created = _request(
                client,
                "POST",
                "/work-orders",
                json={
                    "house_id": int(private["org"]["house_id"]),
                    "text": f"Assignment SLA synthetic electrical probe {suffix}",
                    "category": "ELECTRICAL",
                    "priority": "URGENT",
                    "severity": "LOW",
                },
                headers={
                    **_auth(resident_token, f"sla-create-{suffix}"),
                    "Idempotency-Key": f"sla-accept-respond-004-{suffix}",
                },
            )
            work_order_id = int(created["id"])
            first_assignment = _wait_assignment(engine, work_order_id)
            if first_assignment["employee_id"] not in employee_tokens:
                raise RuntimeError("first auto assignment used an unexpected employee")
            first_clock = _load_clock(engine, work_order_id)
            accept_timeout = _sweep_assignment(
                engine,
                settings,
                work_order_id,
                first_clock["accept_due_at"],
            )
            duplicate_accept_timeout = _sweep_assignment(
                engine,
                settings,
                work_order_id,
                first_clock["accept_due_at"],
            )
            second_assignment = _wait_assignment(
                engine,
                work_order_id,
                exclude_id=int(first_assignment["assignment_id"]),
            )
            if second_assignment["employee_id"] not in employee_tokens:
                raise RuntimeError("automatic redispatch used an unexpected employee")
            if second_assignment["employee_id"] == first_assignment["employee_id"]:
                raise RuntimeError("timed-out employee was immediately selected again")
            second_clock = _load_clock(engine, work_order_id)
            accepted = _request(
                client,
                "POST",
                f"/staff/work-orders/{work_order_id}/accept",
                json={"version": int(second_assignment["work_order_version"])},
                headers=_auth(
                    employee_tokens[int(second_assignment["employee_id"])],
                    f"sla-accept-{suffix}",
                ),
            )
            accepted_version = int(accepted["version"])
            accepted_clock = _load_clock(engine, work_order_id)
            response_timeout = _sweep_assignment(
                engine,
                settings,
                work_order_id,
                accepted_clock["respond_due_at"],
            )
            duplicate_response_timeout = _sweep_assignment(
                engine,
                settings,
                work_order_id,
                accepted_clock["respond_due_at"],
            )
            _wait_phase_deliveries(engine, work_order_id, expected_minimum=4)
            inbox = _request(
                client,
                "GET",
                "/notifications",
                params={"limit": 100},
                headers=_auth(manager_token, f"sla-inbox-{suffix}"),
            )
            manager_titles = sorted(
                item["title"]
                for item in inbox["items"]
                if item["resource_type"] == "WORK_ORDER"
                and int(item["resource_id"]) == work_order_id
                and item["title"] in PHASE_TITLES
            )

        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            revision = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            record = session.get(WorkOrder, work_order_id)
            assignments = session.execute(
                text(
                    "SELECT id, employee_id, status, assigned_source, assigned_at, "
                    "accept_at, timeout_at, ended_at, reason "
                    "FROM work_order_assignments WHERE work_order_id = :work_order_id "
                    "ORDER BY id"
                ),
                {"work_order_id": work_order_id},
            ).mappings().all()
            phase_logs = session.scalars(
                select(WorkOrderLog)
                .where(
                    WorkOrderLog.work_order_id == work_order_id,
                    WorkOrderLog.request_id.like("sla-%"),
                )
                .order_by(WorkOrderLog.id)
            ).all()
            deliveries = session.execute(
                select(
                    Notification.title,
                    NotificationDelivery.status,
                    NotificationDelivery.attempt,
                )
                .join(
                    NotificationDelivery,
                    NotificationDelivery.notification_id == Notification.id,
                )
                .where(
                    Notification.resource_type == "WORK_ORDER",
                    Notification.resource_id == work_order_id,
                    Notification.title.in_(PHASE_TITLES),
                )
                .order_by(Notification.id)
            ).all()
            daily_rows = session.execute(
                select(
                    EmployeeDailyStatus.employee_id,
                    EmployeeDailyStatus.current_load,
                    EmployeeDailyStatus.on_duty,
                    EmployeeDailyStatus.business_date,
                )
                .where(
                    EmployeeDailyStatus.employee_id.in_(
                        (primary_employee_id, full_employee_id)
                    ),
                    EmployeeDailyStatus.property_id
                    == int(private["org"]["property_id"]),
                )
                .order_by(
                    EmployeeDailyStatus.employee_id,
                    EmployeeDailyStatus.business_date.desc(),
                )
            ).all()
            if record is None:
                raise RuntimeError("created work order disappeared")

        evidence = {
            "task_id": "SLA-ACCEPT-RESPOND-REASSIGN-004",
            "captured_at": utc_now().isoformat(),
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": revision,
                "queue": "property_ai.auth001",
                "beat_started": False,
                "analysis_provider": "DEVELOPMENT/Fake",
                "notification_provider": "DEVELOPMENT",
            },
            "business_object": {
                "work_order_id": work_order_id,
                "created_via": "resident SMS login plus POST /work-orders",
                "final_status": record.status,
                "final_version": record.version,
                "accepted_version": accepted_version,
            },
            "capacity_precondition": {
                "updated_via": "PATCH /admin/employees/{employee_id}",
                "employee_ids": sorted(employee_tokens),
                "max_capacity": 100,
                "direct_sql_business_write": False,
            },
            "accept_timeout": {
                "first_assignment": {
                    **first_assignment,
                    "assigned_at": _iso(first_assignment["assigned_at"]),
                },
                "deadline": _iso(first_clock["accept_due_at"]),
                "deadline_seconds_from_assignment": (
                    first_clock["accept_due_at"]
                    - first_assignment["assigned_at"]
                ).total_seconds(),
                "sweep": accept_timeout,
                "duplicate_sweep": duplicate_accept_timeout,
                "second_assignment": {
                    **second_assignment,
                    "assigned_at": _iso(second_assignment["assigned_at"]),
                },
                "second_deadline": _iso(second_clock["accept_due_at"]),
                "second_deadline_restarted": (
                    second_clock["accept_due_at"] > first_clock["accept_due_at"]
                ),
                "employee_changed": (
                    second_assignment["employee_id"]
                    != first_assignment["employee_id"]
                ),
            },
            "response_timeout": {
                "accepted_at": _iso(record.accepted_at),
                "deadline": _iso(accepted_clock["respond_due_at"]),
                "deadline_seconds_from_accept": (
                    accepted_clock["respond_due_at"] - record.accepted_at
                ).total_seconds(),
                "sweep": response_timeout,
                "duplicate_sweep": duplicate_response_timeout,
                "status_preserved": record.status == "ACCEPTED",
                "assignment_preserved": record.current_assignment_id
                == second_assignment["assignment_id"],
            },
            "persistence": {
                "assignments": [
                    {
                        key: _iso(value) if isinstance(value, datetime) else value
                        for key, value in row.items()
                    }
                    for row in assignments
                ],
                "phase_logs": [
                    {
                        "event_code": item.event_code,
                        "request_id": item.request_id,
                        "from_status": item.from_status,
                        "to_status": item.to_status,
                        "reason_code": (item.payload_json or {}).get("reason_code"),
                        "action": (item.payload_json or {}).get("action"),
                    }
                    for item in phase_logs
                ],
                "notification_deliveries": [
                    {
                        "title": row.title,
                        "status": row.status,
                        "attempt": row.attempt,
                    }
                    for row in deliveries
                ],
                "manager_inbox_titles": manager_titles,
                "daily_status": [
                    {
                        "employee_id": row.employee_id,
                        "current_load": row.current_load,
                        "on_duty": row.on_duty,
                        "business_date": row.business_date.isoformat(),
                    }
                    for row in daily_rows
                ],
            },
            "boundaries": {
                "synthetic_data_only": True,
                "targeted_scheduler_work_order": True,
                "historical_rows_swept": False,
                "wall_clock_beat_observed": False,
                "actual_page_automation": False,
                "production_provider_verified": False,
                "direct_business_status_sql": False,
                "migration_run": False,
                "port_8082_touched": False,
            },
            "redaction": (
                "No phone, OTP, token, password, secret, resident name, or user id "
                "is written to evidence."
            ),
        }
        OUTPUT.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "task_id": evidence["task_id"],
                    "work_order_id": work_order_id,
                    "final_status": record.status,
                    "employee_changed": evidence["accept_timeout"]["employee_changed"],
                    "response_status_preserved": evidence["response_timeout"][
                        "status_preserved"
                    ],
                    "evidence": str(OUTPUT.relative_to(REPO)),
                },
                ensure_ascii=False,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
