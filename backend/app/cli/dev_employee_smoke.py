"""Development-only Task 5 attendance smoke. Never invoked by startup."""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.governance import AuditAction
from app.domain.dev import DEV_PROPERTY_NAME
from app.main import create_app
from app.model.employee import AttendanceRecord, Employee, EmployeeDailyStatus
from app.model.identity import AuditLog, User
from app.model.property import Property
from app.service.dev_seed import require_dev_seed_environment

_BINARY_COLLATION = "utf8mb4_bin"


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
        if staff is None or manager is None or employee is None or property_record is None:
            print(
                f"{DevSeedErrorCode.DATA_CONFLICT}: Development employee fixture is missing",
                file=sys.stderr,
            )
            return 1
        staff_id = staff.id
        manager_id = manager.id
        employee_id = employee.id
        property_id = property_record.id
        tenant_id = property_record.tenant_id
    finally:
        session.close()

    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v1/healthz")
        ready = client.get("/api/v1/readyz")
        staff_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": staff_id},
            headers={"X-Request-Id": "t5-smoke-staff-login"},
        ).json()["data"]["access_token"]
        manager_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": manager_id},
            headers={"X-Request-Id": "t5-smoke-manager-login"},
        ).json()["data"]["access_token"]
        identity = client.get(
            f"/api/v1/admin/employees/{employee_id}",
            headers={"Authorization": f"Bearer {manager_token}"},
        )
        checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": property_id},
            headers={
                "Authorization": f"Bearer {staff_token}",
                "X-Request-Id": "t5-smoke-checkin",
            },
        )
        today_on = client.get(
            "/api/v1/staff/attendance/today",
            params={"property_id": property_id},
            headers={"Authorization": f"Bearer {staff_token}"},
        )
        daily = client.get(
            "/api/v1/supervisor/staff/daily",
            params={"tenant_id": tenant_id, "property_id": property_id},
            headers={"Authorization": f"Bearer {manager_token}"},
        )
        checkout = client.post(
            "/api/v1/staff/attendance/checkout",
            json={"property_id": property_id},
            headers={
                "Authorization": f"Bearer {staff_token}",
                "X-Request-Id": "t5-smoke-checkout",
            },
        )
        today_off = client.get(
            "/api/v1/staff/attendance/today",
            params={"property_id": property_id},
            headers={"Authorization": f"Bearer {staff_token}"},
        )

    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        attendance = session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.property_id == property_id,
            )
        )
        daily_row = session.scalar(
            select(EmployeeDailyStatus).where(
                EmployeeDailyStatus.employee_id == employee_id,
                EmployeeDailyStatus.property_id == property_id,
            )
        )
        audits = {
            item.action
            for item in session.scalars(
                select(AuditLog).where(
                    AuditLog.request_id.in_(
                        ("t5-smoke-checkin", "t5-smoke-checkout")
                    )
                )
            )
        }
    finally:
        session.close()
        engine.dispose()

    checks = (
        health.status_code == 200,
        ready.status_code == 200,
        identity.status_code == 200,
        identity.json()["data"]["employee"]["user_id"] == staff_id,
        checkin.status_code == 200,
        checkin.json()["data"]["daily_status"]["on_duty"] == "ON_DUTY",
        today_on.json()["data"]["daily_status"]["on_duty"] == "ON_DUTY",
        daily.status_code == 200,
        any(item["on_duty"] == "ON_DUTY" for item in daily.json()["data"]["items"]),
        checkout.status_code == 200,
        today_off.json()["data"]["daily_status"]["on_duty"] == "OFF_DUTY",
        attendance is not None and attendance.check_out_at is not None,
        daily_row is not None and daily_row.on_duty == "OFF_DUTY",
        attendance is not None
        and daily_row is not None
        and attendance.business_date == daily_row.business_date,
        AuditAction.ATTENDANCE_CHECKED_IN.value in audits,
        AuditAction.ATTENDANCE_CHECKED_OUT.value in audits,
    )
    if not all(checks):
        print("Development employee smoke failed", file=sys.stderr)
        return 1
    print(
        "Development employee smoke passed "
        f"employee_id={employee_id} business_date={attendance.business_date}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
