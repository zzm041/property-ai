"""Development-only Task 6 WorkOrder smoke. Never invoked by startup."""

from __future__ import annotations

import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.governance import AuditAction
from app.domain.dev import DEV_PROPERTY_NAME
from app.main import create_app
from app.model.employee import Employee
from app.model.identity import AuditLog, User
from app.model.property import House, Property
from app.model.work_order import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderLog,
    WorkOrderMedia,
)
from app.service.dev_house_fixture import DEV_HOUSE_ROOM_NO
from app.service.dev_seed import require_dev_seed_environment

_BINARY_COLLATION = "utf8mb4_bin"


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = get_settings()
    try:
        require_dev_seed_environment(settings)
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    if not settings.dev_auth_enabled:
        print(
            f"{DevSeedErrorCode.ENVIRONMENT_FORBIDDEN}: Development Auth is required",
            file=sys.stderr,
        )
        return 1

    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        resident = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_RESIDENT__"
            )
        )
        staff = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_STAFF__"
            )
        )
        manager = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION)
                == "__DEV_PROPERTY_MANAGER__"
            )
        )
        employee = None if staff is None else session.scalar(
            select(Employee).where(Employee.user_id == staff.id)
        )
        property_record = None if employee is None else session.scalar(
            select(Property).where(
                Property.tenant_id == employee.tenant_id,
                Property.name.collate(_BINARY_COLLATION) == DEV_PROPERTY_NAME,
            )
        )
        house = None if property_record is None else session.scalar(
            select(House).where(
                House.property_id == property_record.id,
                House.room_no.collate(_BINARY_COLLATION) == DEV_HOUSE_ROOM_NO,
            )
        )
        if (
            resident is None
            or staff is None
            or manager is None
            or employee is None
            or property_record is None
            or house is None
        ):
            print(
                f"{DevSeedErrorCode.DATA_CONFLICT}: Development work-order fixture is missing",
                file=sys.stderr,
            )
            return 1
        ids = {
            "resident": resident.id,
            "staff": staff.id,
            "manager": manager.id,
            "employee": employee.id,
            "property": property_record.id,
            "house": house.id,
            "tenant": property_record.tenant_id,
        }
    finally:
        session.close()

    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v1/healthz")
        ready = client.get("/api/v1/readyz")
        resident_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["resident"]},
            headers={"X-Request-Id": "t6-smoke-resident-login"},
        ).json()["data"]["access_token"]
        staff_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["staff"]},
            headers={"X-Request-Id": "t6-smoke-staff-login"},
        ).json()["data"]["access_token"]
        manager_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["manager"]},
            headers={"X-Request-Id": "t6-smoke-manager-login"},
        ).json()["data"]["access_token"]

        checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": ids["property"]},
            headers=_auth(staff_token, "t6-smoke-checkin"),
        )
        if checkin.status_code != 200:
            today = client.get(
                "/api/v1/staff/attendance/today",
                params={"property_id": ids["property"]},
                headers=_auth(staff_token, "t6-smoke-today"),
            )
            on_duty = (
                today.status_code == 200
                and (today.json().get("data") or {})
                .get("daily_status", {})
                .get("on_duty")
                == "ON_DUTY"
            )
            if not on_duty:
                print(
                    "Development work-order smoke failed: staff is not on duty",
                    file=sys.stderr,
                )
                return 1
        created = client.post(
            "/api/v1/work-orders",
            json={"house_id": ids["house"], "text": "Kitchen leak"},
            headers={
                **_auth(resident_token, "t6-smoke-create"),
                "Idempotency-Key": f"t6-smoke-{uuid4()}",
            },
        )
        work_order_id = created.json().get("data", {}).get("id")
        queue = client.get(
            "/api/v1/supervisor/work-orders",
            params={"property_id": ids["property"]},
            headers=_auth(manager_token, "t6-smoke-queue"),
        )
        assigned = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": ids["employee"]},
            headers=_auth(manager_token, "t6-smoke-assign"),
        )
        mine = client.get(
            "/api/v1/staff/work-orders",
            headers=_auth(staff_token, "t6-smoke-mine"),
        )
        accepted = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff_token, "t6-smoke-accept"),
        )
        started = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff_token, "t6-smoke-start"),
        )
        before = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 12,
                "property_id": ids["property"],
                "original_name": "before.jpg",
            },
            headers=_auth(staff_token, "t6-smoke-file-before"),
        )
        after = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 12,
                "property_id": ids["property"],
                "original_name": "after.jpg",
            },
            headers=_auth(staff_token, "t6-smoke-file-after"),
        )
        before_id = before.json().get("data", {}).get("id")
        after_id = after.json().get("data", {}).get("id")
        feedback = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/feedback",
            json={
                "text": "Valve replaced",
                "file_ids": [before_id] if before_id else [],
                "taken_phase": "BEFORE",
            },
            headers=_auth(staff_token, "t6-smoke-feedback"),
        )
        resolved = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "PLUMBING",
                "method": "Replace valve",
                "resolved": True,
                "file_ids": [after_id] if after_id else [],
            },
            headers=_auth(staff_token, "t6-smoke-resolve"),
        )
        viewed = client.get(
            f"/api/v1/work-orders/{work_order_id}",
            headers=_auth(resident_token, "t6-smoke-view"),
        )
        reopened = client.post(
            f"/api/v1/work-orders/{work_order_id}/reopen",
            json={"reason": "Still leaking"},
            headers=_auth(resident_token, "t6-smoke-reopen"),
        )
        reassigned = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": ids["employee"], "reason": "Continue repair"},
            headers=_auth(manager_token, "t6-smoke-reassign"),
        )
        accepted2 = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff_token, "t6-smoke-accept-2"),
        )
        started2 = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff_token, "t6-smoke-start-2"),
        )
        resolved2 = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "PLUMBING",
                "method": "Tighten joint",
                "resolved": True,
            },
            headers=_auth(staff_token, "t6-smoke-resolve-2"),
        )
        confirmed = client.post(
            f"/api/v1/work-orders/{work_order_id}/confirm",
            json={},
            headers=_auth(resident_token, "t6-smoke-confirm"),
        )

    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        work_order = None if work_order_id is None else session.get(
            WorkOrder, work_order_id
        )
        assignments = [] if work_order_id is None else list(
            session.scalars(
                select(WorkOrderAssignment).where(
                    WorkOrderAssignment.work_order_id == work_order_id
                )
            )
        )
        logs = [] if work_order_id is None else list(
            session.scalars(
                select(WorkOrderLog).where(WorkOrderLog.work_order_id == work_order_id)
            )
        )
        media = [] if work_order_id is None else list(
            session.scalars(
                select(WorkOrderMedia).where(WorkOrderMedia.work_order_id == work_order_id)
            )
        )
        audits = {
            item.action
            for item in session.scalars(
                select(AuditLog).where(
                    AuditLog.request_id.in_(
                        (
                            "t6-smoke-create",
                            "t6-smoke-assign",
                            "t6-smoke-reassign",
                            "t6-smoke-start",
                            "t6-smoke-feedback",
                            "t6-smoke-resolve",
                            "t6-smoke-reopen",
                            "t6-smoke-confirm",
                        )
                    )
                )
            )
        }
    finally:
        session.close()
        engine.dispose()

    named_checks = (
        ("healthz", health.status_code == 200),
        ("readyz", ready.status_code == 200),
        ("checkin", checkin.status_code in {200, 409}),
        ("create", created.status_code == 200),
        ("create_status", created.json().get("data", {}).get("status") == "PENDING_ANALYSIS"),
        ("queue", queue.status_code == 200),
        (
            "queue_contains",
            any(
                item["id"] == work_order_id
                for item in queue.json().get("data", {}).get("items", [])
            ),
        ),
        ("assign", assigned.status_code == 200),
        ("assign_status", assigned.json().get("data", {}).get("status") == "ASSIGNED"),
        ("mine", mine.status_code == 200),
        ("accept", accepted.status_code == 200),
        ("start", started.status_code == 200),
        ("file_before", before.status_code == 200),
        ("file_after", after.status_code == 200),
        (
            "storage_key_not_exposed",
            "storage_key" not in before.json().get("data", {}),
        ),
        ("feedback", feedback.status_code == 200),
        ("resolve", resolved.status_code == 200),
        ("view", viewed.status_code == 200),
        (
            "view_status",
            viewed.json().get("data", {}).get("work_order", {}).get("status")
            == "WAIT_CONFIRM",
        ),
        ("reopen", reopened.status_code == 200),
        ("reassign", reassigned.status_code == 200),
        ("accept2", accepted2.status_code == 200),
        ("start2", started2.status_code == 200),
        ("resolve2", resolved2.status_code == 200),
        ("confirm", confirmed.status_code == 200),
        (
            "confirm_status",
            confirmed.json().get("data", {}).get("status") == "CLOSED",
        ),
        ("db_closed", work_order is not None and work_order.status == "CLOSED"),
        ("assignment_history", len(assignments) >= 2),
        (
            "reassigned_row",
            any(item.status == "REASSIGNED" for item in assignments),
        ),
        ("reopen_log", any(item.event_code == "REOPEN" for item in logs)),
        ("media_before", any(item.taken_phase == "BEFORE" for item in media)),
        ("media_after", any(item.taken_phase == "AFTER" for item in media)),
        ("audit_created", AuditAction.WORK_ORDER_CREATED.value in audits),
        ("audit_assigned", AuditAction.WORK_ORDER_ASSIGNED.value in audits),
        ("audit_reassigned", AuditAction.WORK_ORDER_REASSIGNED.value in audits),
        (
            "audit_started",
            AuditAction.WORK_ORDER_PROCESS_STARTED.value in audits,
        ),
        ("audit_resolved", AuditAction.WORK_ORDER_RESOLVED.value in audits),
        ("audit_reopened", AuditAction.WORK_ORDER_REOPENED.value in audits),
        ("audit_closed", AuditAction.WORK_ORDER_CLOSED.value in audits),
    )
    failed = [name for name, ok in named_checks if not ok]
    if failed:
        print(
            "Development work-order smoke failed: " + ", ".join(failed),
            file=sys.stderr,
        )
        return 1
    print(
        "Development work-order smoke passed "
        f"work_order_id={work_order_id} status=CLOSED"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
