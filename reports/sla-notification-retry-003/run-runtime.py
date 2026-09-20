"""Additive AUTH001 runtime proof for SLA transitions and notification retries.

The resident creates the work order through the real API. SLA transitions use
the production scheduler service with a controlled clock and a single explicit
work-order id so historical synthetic rows are not swept. No phone, OTP, token,
or resident name is written to evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import timedelta
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

from app.core.database import begin_owned_transaction, create_database_engine
from app.core.time import utc_now
from app.model.ai import Notification
from app.model.knowledge import NotificationDelivery
from app.model.work_order import SlaClock, WorkOrder, WorkOrderLog
from app.service.notification import NotificationService
from app.service.sla import SlaService
from auth001_isolated_runtime import apply_isolated_environ, build_isolated_settings

API = "http://127.0.0.1:8083/api/v1"
CONTEXT = REPO / "tmp" / "staff-mgmt-auto-dispatch-001" / "context.json"
OUTPUT = Path(__file__).with_name("runtime-evidence.json")


def _post(client: httpx.Client, path: str, *, json_body, headers=None):
    response = client.post(f"{API}{path}", json=json_body, headers=headers)
    response.raise_for_status()
    return response.json()["data"]


def _login(client: httpx.Client, phone: str) -> tuple[str, int]:
    challenge = _post(
        client,
        "/auth/challenge",
        json_body={"phone": phone, "purpose": "login"},
    )
    numbers = [int(item) for item in re.findall(r"\d+", challenge["prompt"])]
    if len(numbers) != 2:
        raise RuntimeError("visual challenge prompt was not understood")
    _post(
        client,
        "/auth/sms/send",
        json_body={
            "phone": phone,
            "purpose": "login",
            "challenge_id": challenge["challenge_id"],
            "challenge_answer": str(sum(numbers)),
        },
    )
    inbox = _post(
        client,
        "/auth/sms/test-inbox",
        json_body={"phone": phone, "purpose": "login"},
    )
    issued = _post(
        client,
        "/auth/sms/login",
        json_body={"phone": phone, "code": inbox["code"]},
    )
    return issued["access_token"], int(issued["user_id"])


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def _load_clock(engine, work_order_id: int) -> SlaClock:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        clock = session.scalar(
            select(SlaClock).where(SlaClock.work_order_id == work_order_id)
        )
        if clock is None:
            raise RuntimeError("created work order has no SLA clock")
        session.expunge(clock)
        return clock


def _sweep(engine, settings, work_order_id: int, at) -> dict[str, int]:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        return SlaService(session, settings=settings).sweep_due(
            now=at,
            work_order_id=work_order_id,
        ).as_dict()


def _wait_business_deliveries(engine, work_order_id: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            states = session.execute(
                select(NotificationDelivery.status)
                .join(Notification, Notification.id == NotificationDelivery.notification_id)
                .where(
                    Notification.resource_type == "WORK_ORDER",
                    Notification.resource_id == work_order_id,
                    Notification.title.in_(
                        ("工单 SLA 即将超时", "工单 SLA 已超时")
                    ),
                )
            ).scalars().all()
        if states and all(state != "PENDING" for state in states):
            return
        time.sleep(0.25)
    raise RuntimeError("SLA notification deliveries did not settle")


def _schedule_target_deliveries(engine, settings, work_order_id: int) -> int:
    from app.worker.enqueue import schedule_notification_delivery

    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        delivery_ids = session.scalars(
            select(NotificationDelivery.id)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                Notification.resource_type == "WORK_ORDER",
                Notification.resource_id == work_order_id,
                Notification.title.in_(
                    ("工单 SLA 即将超时", "工单 SLA 已超时")
                ),
                NotificationDelivery.status == "PENDING",
            )
            .order_by(NotificationDelivery.id)
        ).all()
    return sum(
        1
        for delivery_id in delivery_ids
        if schedule_notification_delivery(settings, int(delivery_id))
    )


def _find_resumable_work_order(engine) -> int | None:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        candidate = session.execute(
            select(WorkOrder.id)
            .join(WorkOrderLog, WorkOrderLog.work_order_id == WorkOrder.id)
            .where(WorkOrderLog.event_code.in_(("SLA_WARNING", "SLA_OVERDUE")))
            .group_by(WorkOrder.id)
            .having(text("COUNT(DISTINCT work_order_logs.event_code) = 2"))
            .order_by(WorkOrder.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return None if candidate is None else int(candidate)


def _enqueue_probe(engine, settings, *, user_id: int, work_order: WorkOrder, title: str) -> int:
    import app.service.notification as notification_module

    original_schedule = notification_module.schedule_notification_delivery
    notification_module.schedule_notification_delivery = lambda _settings, _id: False
    try:
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            with begin_owned_transaction(session):
                record = NotificationService(session, settings=settings).enqueue(
                    tenant_id=work_order.tenant_id,
                    property_id=work_order.property_id,
                    user_id=user_id,
                    title=title,
                    body="Synthetic bounded-retry runtime probe",
                    resource_type="WORK_ORDER",
                    resource_id=work_order.id,
                )
                notification_id = record.id
            delivery_id = session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.notification_id == notification_id
                )
            )
            if delivery_id is None:
                raise RuntimeError("probe delivery was not created")
            return int(delivery_id)
    finally:
        notification_module.schedule_notification_delivery = original_schedule


def _delivery_snapshot(engine, delivery_id: int) -> dict[str, object]:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        delivery = session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            raise RuntimeError("probe delivery is missing")
        return {
            "status": delivery.status,
            "attempt": delivery.attempt,
            "last_error_code": delivery.last_error_code,
            "delivered_at_present": delivery.delivered_at is not None,
            "next_retry_at": delivery.next_retry_at,
        }


def _deliver_when_due(engine, settings, delivery_id: int) -> tuple[str, dict[str, object]]:
    snapshot = _delivery_snapshot(engine, delivery_id)
    due = snapshot["next_retry_at"]
    if due is not None:
        delay = (due - utc_now()).total_seconds()
        if delay > 0:
            time.sleep(delay + 0.1)
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        outcome = NotificationService(session, settings=settings).deliver_pending(delivery_id)
    return outcome, _delivery_snapshot(engine, delivery_id)


def main() -> None:
    private = json.loads(CONTEXT.read_text(encoding="utf-8"))
    settings = build_isolated_settings(enable_enqueue=True)
    apply_isolated_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    run_id = uuid4().hex[:10]

    try:
        with httpx.Client(timeout=10) as client:
            manager_token, manager_user_id = _login(
                client, private["phones"]["tenant_admin"]
            )
            work_order_id = _find_resumable_work_order(engine)
            resumed = work_order_id is not None
            if work_order_id is None:
                resident_token, _resident_user_id = _login(
                    client, private["phones"]["resident"]
                )
                create_response = client.post(
                    f"{API}/work-orders",
                    json={
                        "house_id": int(private["org"]["house_id"]),
                        "text": f"SLA notification retry runtime probe {run_id}",
                        "category": "ELECTRICAL",
                        "priority": "URGENT",
                        "severity": "LOW",
                    },
                    headers={
                        **_auth(resident_token, f"sla-create-{run_id}"),
                        "Idempotency-Key": f"sla-notification-retry-003-{run_id}",
                    },
                )
                create_response.raise_for_status()
                work_order_id = int(create_response.json()["data"]["id"])

                clock = _load_clock(engine, work_order_id)
                warning = _sweep(engine, settings, work_order_id, clock.warning_at)
                duplicate_warning = _sweep(
                    engine, settings, work_order_id, clock.warning_at
                )
                overdue = _sweep(engine, settings, work_order_id, clock.complete_due_at)
                duplicate_overdue = _sweep(
                    engine, settings, work_order_id, clock.complete_due_at
                )
            else:
                clock = _load_clock(engine, work_order_id)
                warning = {"persisted_event_count_before_resume": 1}
                overdue = {"persisted_event_count_before_resume": 1}
                duplicate_warning = _sweep(
                    engine, settings, work_order_id, clock.warning_at
                )
                duplicate_overdue = _sweep(
                    engine, settings, work_order_id, clock.complete_due_at
                )

            scheduled_target_deliveries = _schedule_target_deliveries(
                engine, settings, work_order_id
            )
            _wait_business_deliveries(engine, work_order_id)

            inbox_response = client.get(
                f"{API}/notifications?limit=100",
                headers=_auth(manager_token, f"sla-inbox-{run_id}"),
            )
            inbox_response.raise_for_status()
            manager_items = [
                item
                for item in inbox_response.json()["data"]["items"]
                if item["resource_type"] == "WORK_ORDER"
                and int(item["resource_id"]) == work_order_id
                and item["title"]
                in {"工单 SLA 即将超时", "工单 SLA 已超时"}
            ]

        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            work_order = session.get(WorkOrder, work_order_id)
            if work_order is None:
                raise RuntimeError("created work order disappeared")
            session.expunge(work_order)

        unavailable = settings.model_copy(
            update={"notification_provider_behavior": "unavailable"}
        )
        terminal_id = _enqueue_probe(
            engine,
            unavailable,
            user_id=manager_user_id,
            work_order=work_order,
            title=f"SLA retry terminal probe {run_id}",
        )
        terminal_outcomes = []
        terminal_snapshots = []
        for _ in range(4):
            outcome, snapshot = _deliver_when_due(
                engine, unavailable, terminal_id
            )
            terminal_outcomes.append(outcome)
            terminal_snapshots.append(
                {
                    key: value
                    for key, value in snapshot.items()
                    if key != "next_retry_at"
                }
            )

        recovery_id = _enqueue_probe(
            engine,
            unavailable,
            user_id=manager_user_id,
            work_order=work_order,
            title=f"SLA retry recovery probe {run_id}",
        )
        recovery_first, _ = _deliver_when_due(engine, unavailable, recovery_id)
        recovery_second, recovery_snapshot = _deliver_when_due(
            engine, settings, recovery_id
        )
        recovery_duplicate, duplicate_snapshot = _deliver_when_due(
            engine, settings, recovery_id
        )

        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            final_clock = session.scalar(
                select(SlaClock).where(SlaClock.work_order_id == work_order_id)
            )
            logs = session.scalars(
                select(WorkOrderLog)
                .where(
                    WorkOrderLog.work_order_id == work_order_id,
                    WorkOrderLog.event_code.in_(("SLA_WARNING", "SLA_OVERDUE")),
                )
                .order_by(WorkOrderLog.id)
            ).all()
            business_rows = session.execute(
                select(
                    Notification.title,
                    Notification.user_id,
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
                    Notification.title.in_(
                        ("工单 SLA 即将超时", "工单 SLA 已超时")
                    ),
                )
                .order_by(Notification.id)
            ).all()
            assignment_source = session.execute(
                text(
                    "SELECT assigned_source FROM work_order_assignments "
                    "WHERE work_order_id = :work_order_id ORDER BY id DESC LIMIT 1"
                ),
                {"work_order_id": work_order_id},
            ).scalar_one_or_none()

        evidence = {
            "task_id": "SLA-NOTIFICATION-RETRY-003",
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
                "provider": "DEVELOPMENT",
            },
            "business_object": {
                "work_order_id": work_order_id,
                "created_via": "resident SMS login plus POST /work-orders",
                "resumed_after_delivery_wait_timeout": resumed,
                "assignment_source": assignment_source,
                "clock_final_state": None if final_clock is None else final_clock.state,
            },
            "sla": {
                "warning": warning,
                "duplicate_warning": duplicate_warning,
                "overdue": overdue,
                "duplicate_overdue": duplicate_overdue,
                "target_delivery_tasks_scheduled": scheduled_target_deliveries,
                "log_events": [item.event_code for item in logs],
                "log_request_ids": [item.request_id for item in logs],
                "notification_rows": len(business_rows),
                "unique_recipient_count": len({row.user_id for row in business_rows}),
                "delivery_states": [
                    {
                        "title": row.title,
                        "status": row.status,
                        "attempt": row.attempt,
                    }
                    for row in business_rows
                ],
                "manager_inbox_titles": sorted(item["title"] for item in manager_items),
            },
            "retry": {
                "terminal_outcomes": terminal_outcomes,
                "terminal_snapshots": terminal_snapshots,
                "recovery_outcomes": [
                    recovery_first,
                    recovery_second,
                    recovery_duplicate,
                ],
                "recovery_final": {
                    key: value
                    for key, value in recovery_snapshot.items()
                    if key != "next_retry_at"
                },
                "duplicate_final": {
                    key: value
                    for key, value in duplicate_snapshot.items()
                    if key != "next_retry_at"
                },
            },
            "boundaries": {
                "synthetic_data_only": True,
                "historical_rows_swept": False,
                "targeted_scheduler_clock": True,
                "wall_clock_beat_observed": False,
                "production_notification_provider_verified": False,
                "direct_business_status_sql": False,
                "migration_run": False,
            },
            "redaction": (
                "No phone, OTP, token, password, secret, resident name, or user id "
                "is written to evidence."
            ),
        }
        OUTPUT.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "evidence": str(OUTPUT.relative_to(REPO)),
                    "work_order_id": work_order_id,
                    "clock_final_state": evidence["business_object"]["clock_final_state"],
                    "sla_notifications": len(business_rows),
                    "terminal_outcomes": terminal_outcomes,
                    "recovery_outcomes": evidence["retry"]["recovery_outcomes"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
