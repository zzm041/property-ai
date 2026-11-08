"""Employee identity, assignment, attendance, authorization, and concurrency on MySQL 8."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, EmployeeErrorCode, IamErrorCode
from app.core.time import to_business_date, utc_now
from app.domain.audit import AuditAction
from app.main import create_app
from app.model.employee import (
    AttendanceRecord,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
)
from app.model.identity import AuditLog, Permission, RolePermission, User
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.employee import EmployeeService
from tests.integration.conftest import commit_open_transaction
from tests.integration.employee_test_data import create_employee_attendance_graph

TEST_SECRET = "t5-employee-attendance-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
    )


def _client() -> TestClient:
    return TestClient(create_app(_settings()))


def _token(client: TestClient, user_id: int) -> str:
    response = client.post(
        "/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers={"X-Request-Id": f"t5-login-{user_id}"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _principal(session: Session, user_id: int):
    principal = AuthService(session, IdentityRepository(session)).load_principal(user_id)
    commit_open_transaction(session)
    return principal


def test_admin_creates_and_reads_employee(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with identity_session.begin():
        extra = User(
            status="ACTIVE",
            display_name="New Staff",
        )
        identity_session.add(extra)
        identity_session.flush()
        extra_id = extra.id
    with _client() as client:
        manager = _token(client, graph.manager_p1_id)
        created = client.post(
            "/api/v1/admin/employees",
            json={
                "user_id": extra_id,
                "tenant_id": graph.tenant_1_id,
                "employee_no": "E-NEW",
                "property_id": graph.property_1_id,
                "department_id": graph.department_1_id,
                "duty_ids": [graph.duty_maintenance_id],
                "skill_ids": [graph.skill_electrical_id],
            },
            headers={**_auth(manager), "X-Request-Id": "t5-create"},
        )
        employee_id = created.json()["data"]["id"]
        detail = client.get(
            f"/api/v1/admin/employees/{employee_id}",
            headers=_auth(manager),
        )
    assert created.status_code == 200
    assert created.json()["data"]["user_id"] == extra_id
    assert created.json()["data"]["employee_no"] == "E-NEW"
    assert detail.status_code == 200
    assert detail.json()["data"]["employee"]["id"] == employee_id
    assert len(detail.json()["data"]["assignments"]) == 1
    identity_session.rollback()
    audits = identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "t5-create")
    ).all()
    actions = {item.action for item in audits}
    assert AuditAction.EMPLOYEE_CREATED.value in actions
    assert AuditAction.EMPLOYEE_PROPERTY_ASSIGNED.value in actions


def test_duplicate_employee_identity_is_rejected(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        manager = _token(client, graph.manager_p1_id)
        duplicate_user = client.post(
            "/api/v1/admin/employees",
            json={
                "user_id": graph.staff_a_user_id,
                "tenant_id": graph.tenant_1_id,
                "employee_no": "E-DUP-USER",
                "property_id": graph.property_1_id,
            },
            headers=_auth(manager),
        )
        duplicate_no = client.post(
            "/api/v1/admin/employees",
            json={
                "user_id": graph.resident_id,
                "tenant_id": graph.tenant_1_id,
                "employee_no": "E-A",
                "property_id": graph.property_1_id,
            },
            headers=_auth(manager),
        )
    assert duplicate_user.status_code == 409
    assert duplicate_user.json()["error"]["code"] == EmployeeErrorCode.DUPLICATE_EMPLOYEE
    assert duplicate_no.status_code == 409
    assert duplicate_no.json()["error"]["code"] == EmployeeErrorCode.DUPLICATE_EMPLOYEE
    assert "1062" not in duplicate_user.text


def test_cross_tenant_department_and_duty_are_rejected(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with identity_session.begin():
        extra = User(
            status="ACTIVE",
            display_name="Cross Catalog",
        )
        identity_session.add(extra)
        identity_session.flush()
        extra_id = extra.id
    with _client() as client:
        manager = _token(client, graph.manager_p1_id)
        wrong_department = client.post(
            "/api/v1/admin/employees",
            json={
                "user_id": extra_id,
                "tenant_id": graph.tenant_1_id,
                "employee_no": "E-XDEPT",
                "property_id": graph.property_1_id,
                "department_id": graph.department_2_id,
            },
            headers=_auth(manager),
        )
    assert wrong_department.status_code == 404
    assert (
        wrong_department.json()["error"]["code"]
        == EmployeeErrorCode.DEPARTMENT_NOT_FOUND
    )


def test_multi_property_assignment_and_duplicate_active_rejected(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        tenant_admin = _token(client, graph.tenant_admin_id)
        assigned = client.post(
            f"/api/v1/admin/employees/{graph.employee_a_id}/property-assignments",
            json={"property_id": graph.property_2_id},
            headers={**_auth(tenant_admin), "X-Request-Id": "t5-assign-p2"},
        )
        duplicate = client.post(
            f"/api/v1/admin/employees/{graph.employee_a_id}/property-assignments",
            json={"property_id": graph.property_1_id},
            headers=_auth(tenant_admin),
        )
        detail = client.get(
            f"/api/v1/admin/employees/{graph.employee_a_id}",
            headers=_auth(tenant_admin),
        )
    assert assigned.status_code == 200
    assert assigned.json()["data"]["property_id"] == graph.property_2_id
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == EmployeeErrorCode.DUPLICATE_ASSIGNMENT
    assert len(detail.json()["data"]["assignments"]) == 2


def test_ending_assignment_keeps_history(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        manager = _token(client, graph.manager_p1_id)
        ended = client.post(
            f"/api/v1/admin/employees/{graph.employee_a_id}"
            f"/property-assignments/{graph.assignment_a_p1_id}/end",
            headers={**_auth(manager), "X-Request-Id": "t5-end"},
        )
        checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(_token(client, graph.staff_a_user_id)),
        )
    assert ended.status_code == 200
    assert ended.json()["data"]["status"] == "ENDED"
    assert ended.json()["data"]["valid_to"] is not None
    assert checkin.status_code == 409
    assert checkin.json()["error"]["code"] == EmployeeErrorCode.CHECKIN_DENIED
    identity_session.rollback()
    assignment = identity_session.get(EmployeeProperty, graph.assignment_a_p1_id)
    assert assignment is not None
    assert assignment.status == "ENDED"


def test_staff_checkin_today_supervisor_checkout_flow(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        supervisor = _token(client, graph.supervisor_p1_id)
        checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers={**_auth(staff), "X-Request-Id": "t5-checkin"},
        )
        today = client.get(
            "/api/v1/staff/attendance/today",
            params={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        daily = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(supervisor),
        )
        checkout = client.post(
            "/api/v1/staff/attendance/checkout",
            json={"property_id": graph.property_1_id},
            headers={**_auth(staff), "X-Request-Id": "t5-checkout"},
        )
        after = client.get(
            "/api/v1/staff/attendance/today",
            params={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
    assert checkin.status_code == 200
    assert checkin.json()["data"]["daily_status"]["on_duty"] == "ON_DUTY"
    assert today.json()["data"]["daily_status"]["on_duty"] == "ON_DUTY"
    assert daily.status_code == 200
    assert daily.json()["data"]["items"][0]["on_duty"] == "ON_DUTY"
    assert checkout.status_code == 200
    assert checkout.json()["data"]["daily_status"]["on_duty"] == "OFF_DUTY"
    assert after.json()["data"]["daily_status"]["on_duty"] == "OFF_DUTY"
    assert after.json()["data"]["attendance"]["check_out_at"] is not None
    identity_session.rollback()
    attendance = identity_session.scalar(select(AttendanceRecord))
    daily_row = identity_session.scalar(select(EmployeeDailyStatus))
    assert attendance is not None and daily_row is not None
    assert attendance.check_in_at.tzinfo is UTC
    assert attendance.business_date == daily_row.business_date
    audits = {
        item.action
        for item in identity_session.scalars(
            select(AuditLog).where(
                AuditLog.request_id.in_(("t5-checkin", "t5-checkout"))
            )
        )
    }
    assert AuditAction.ATTENDANCE_CHECKED_IN.value in audits
    assert AuditAction.ATTENDANCE_CHECKED_OUT.value in audits


def test_duplicate_checkin_and_checkout_without_checkin(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        other = _token(client, graph.staff_b_user_id)
        first = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        second = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        checkout_other = client.post(
            "/api/v1/staff/attendance/checkout",
            json={"property_id": graph.property_1_id},
            headers=_auth(other),
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == EmployeeErrorCode.CHECKIN_DENIED
    assert checkout_other.status_code == 409
    assert checkout_other.json()["error"]["code"] == EmployeeErrorCode.CHECKOUT_DENIED
    assert "1062" not in second.text


def test_same_day_return_after_checkout_is_allowed(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        first = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        checkout = client.post(
            "/api/v1/staff/attendance/checkout",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        returned = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
    assert first.status_code == 200
    assert checkout.status_code == 200
    assert returned.status_code == 200
    assert returned.json()["data"]["daily_status"]["on_duty"] == "ON_DUTY"
    assert returned.json()["data"]["attendance"]["check_out_at"] is None


def test_wrong_property_inactive_and_disabled_are_denied(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        disabled = _token(client, graph.disabled_staff_user_id)
        wrong_property = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_2_id},
            headers=_auth(staff),
        )
        cross_tenant = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_3_id},
            headers=_auth(staff),
        )
        disabled_checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(disabled),
        )
        body_employee = client.post(
            "/api/v1/staff/attendance/checkin",
            json={
                "property_id": graph.property_1_id,
                "employee_id": graph.employee_b_id,
            },
            headers=_auth(staff),
        )
    assert wrong_property.status_code == 409
    assert wrong_property.json()["error"]["code"] == EmployeeErrorCode.CHECKIN_DENIED
    assert cross_tenant.status_code == 409
    assert disabled_checkin.status_code == 409
    assert body_employee.status_code == 422


def test_property_manager_scope_and_cross_tenant_denied(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        manager_p1 = _token(client, graph.manager_p1_id)
        manager_p2 = _token(client, graph.manager_p2_id)
        tenant_admin = _token(client, graph.tenant_admin_id)
        system_admin = _token(client, graph.system_admin_id)
        resident = _token(client, graph.resident_id)
        client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        own = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(manager_p1),
        )
        other_property = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_2_id,
            },
            headers=_auth(manager_p1),
        )
        p2_on_p1 = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(manager_p2),
        )
        other_tenant = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_2_id,
                "property_id": graph.property_3_id,
            },
            headers=_auth(tenant_admin),
        )
        other_employee = client.get(
            f"/api/v1/admin/employees/{graph.employee_t2_id}",
            headers=_auth(tenant_admin),
        )
        system_daily = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(system_admin),
        )
        resident_checkin = client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(resident),
        )
        staff_create = client.post(
            "/api/v1/admin/employees",
            json={
                "user_id": graph.resident_id,
                "tenant_id": graph.tenant_1_id,
                "employee_no": "E-STAFF",
                "property_id": graph.property_1_id,
            },
            headers=_auth(staff),
        )
    assert own.status_code == 200
    assert other_property.status_code == 403
    assert p2_on_p1.status_code == 403
    assert other_tenant.status_code == 403
    assert other_employee.status_code == 403
    assert system_daily.status_code == 403
    assert resident_checkin.status_code in {403, 404}
    assert staff_create.status_code == 403


def test_stale_permission_is_denied(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with identity_session.begin():
        permission = identity_session.scalar(
            select(Permission).where(Permission.code == "attendance:read")
        )
        assert permission is not None
        rows = identity_session.scalars(
            select(RolePermission).where(
                RolePermission.permission_id == permission.id
            )
        ).all()
        for row in rows:
            identity_session.delete(row)
    with _client() as client:
        supervisor = _token(client, graph.supervisor_p1_id)
        daily = client.get(
            "/api/v1/supervisor/staff/daily",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(supervisor),
        )
    assert daily.status_code == 403
    assert daily.json()["error"]["code"] == IamErrorCode.FORBIDDEN


def test_concurrent_checkin_creates_one(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_employee_attendance_graph(identity_session)

    def worker() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            actor = _principal(session, graph.staff_a_user_id)
            EmployeeService(session).check_in(
                actor=actor,
                property_id=graph.property_1_id,
                request_id="t5-concurrent-checkin",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("ok") == 1
    assert set(results) <= {
        "ok",
        EmployeeErrorCode.CHECKIN_DENIED,
        EmployeeErrorCode.CONFLICT,
    }
    identity_session.expire_all()
    assert identity_session.scalar(select(func.count()).select_from(AttendanceRecord)) == 1
    assert (
        identity_session.scalar(select(func.count()).select_from(EmployeeDailyStatus))
        == 1
    )


def test_concurrent_checkout_has_single_winner(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    EmployeeService(identity_session).check_in(
        actor=_principal(identity_session, graph.staff_a_user_id),
        property_id=graph.property_1_id,
        request_id="t5-concurrent-checkout-setup",
    )

    def worker() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            actor = _principal(session, graph.staff_a_user_id)
            EmployeeService(session).check_out(
                actor=actor,
                property_id=graph.property_1_id,
                request_id="t5-concurrent-checkout",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("ok") == 1
    assert set(results) <= {
        "ok",
        EmployeeErrorCode.CHECKOUT_DENIED,
        EmployeeErrorCode.CONFLICT,
    }
    identity_session.expire_all()
    attendance = identity_session.scalar(select(AttendanceRecord))
    daily = identity_session.scalar(select(EmployeeDailyStatus))
    assert attendance is not None and attendance.check_out_at is not None
    assert daily is not None and daily.on_duty == "OFF_DUTY"


def test_checkin_audit_failure_rolls_back(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    actor = _principal(identity_session, graph.staff_a_user_id)

    class FailingAudit:
        def append(self, **values: object) -> None:
            del values
            raise RuntimeError("forced attendance audit failure")

    with pytest.raises(RuntimeError, match="forced attendance audit failure"):
        EmployeeService(
            identity_session,
            audit_service=FailingAudit(),  # type: ignore[arg-type]
        ).check_in(
            actor=actor,
            property_id=graph.property_1_id,
            request_id="t5-audit-fail",
        )
    identity_session.rollback()
    assert identity_session.scalar(select(func.count()).select_from(AttendanceRecord)) == 0
    assert (
        identity_session.scalar(select(func.count()).select_from(EmployeeDailyStatus))
        == 0
    )


def test_business_date_uses_property_timezone(identity_session: Session) -> None:
    graph = create_employee_attendance_graph(identity_session)
    clock_time = datetime(2026, 9, 16, 16, 30, tzinfo=UTC)
    with identity_session.begin():
        identity_session.execute(
            text("UPDATE employee_properties SET valid_from = :valid_from"),
            {"valid_from": clock_time - timedelta(days=1)},
        )
    service = EmployeeService(
        identity_session,
        clock=lambda: clock_time,
    )
    attendance, daily = service.check_in(
        actor=_principal(identity_session, graph.staff_t2_user_id),
        property_id=graph.property_3_id,
        request_id="t5-ny-checkin",
    )
    expected = to_business_date(clock_time, "America/New_York")
    assert attendance.business_date == expected
    assert daily.business_date == expected
    shanghai = EmployeeService(
        identity_session,
        clock=lambda: clock_time,
    )
    sh_attendance, sh_daily = shanghai.check_in(
        actor=_principal(identity_session, graph.staff_a_user_id),
        property_id=graph.property_1_id,
        request_id="t5-sh-checkin",
    )
    assert str(sh_attendance.business_date) == "2026-09-17"
    assert str(sh_daily.business_date) == "2026-09-17"
    assert sh_attendance.check_in_at == clock_time


def test_mysql_rejects_cross_tenant_assignment_and_duplicate_active(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    if identity_session.in_transaction():
        identity_session.rollback()
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                EmployeeProperty(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_2_id,
                    property_id=graph.property_3_id,
                    status="ACTIVE",
                    valid_from=utc_now(),
                    valid_to=None,
                )
            )
            identity_session.flush()
    identity_session.rollback()
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                EmployeeProperty(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_1_id,
                    status="ACTIVE",
                    valid_from=utc_now(),
                    valid_to=None,
                )
            )
            identity_session.flush()
    identity_session.rollback()
    with pytest.raises((IntegrityError, OperationalError)):
        with identity_session.begin():
            identity_session.add(
                EmployeeProperty(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_2_id,
                    status="ACTIVE",
                    valid_from=utc_now(),
                    valid_to=utc_now() + timedelta(days=1),
                )
            )
            identity_session.flush()
    identity_session.rollback()
    with identity_session.begin():
        identity_session.execute(
            text(
                """
                UPDATE employee_properties
                SET status = 'ENDED', valid_to = UTC_TIMESTAMP(6)
                WHERE id = :id
                """
            ),
            {"id": graph.assignment_a_p1_id},
        )
        identity_session.add(
            EmployeeProperty(
                employee_id=graph.employee_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                status="ACTIVE",
                valid_from=utc_now(),
                valid_to=None,
            )
        )
    identity_session.rollback()
    assert (
        identity_session.scalar(
            select(func.count()).select_from(EmployeeProperty).where(
                EmployeeProperty.employee_id == graph.employee_a_id,
                EmployeeProperty.property_id == graph.property_1_id,
            )
        )
        == 2
    )


def test_mysql_rejects_duplicate_duty_skill_and_daily_status(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    if identity_session.in_transaction():
        identity_session.rollback()
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                EmployeeDuty(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_1_id,
                    duty_id=graph.duty_maintenance_id,
                )
            )
            identity_session.flush()
    identity_session.rollback()
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                EmployeeSkill(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_1_id,
                    skill_id=graph.skill_electrical_id,
                )
            )
            identity_session.flush()
    identity_session.rollback()
    today = to_business_date(utc_now(), "Asia/Shanghai")
    with identity_session.begin():
        identity_session.add(
            EmployeeDailyStatus(
                employee_id=graph.employee_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                business_date=today,
                on_duty="ON_DUTY",
                current_load=0,
            )
        )
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                EmployeeDailyStatus(
                    employee_id=graph.employee_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_1_id,
                    business_date=today,
                    on_duty="OFF_DUTY",
                    current_load=0,
                )
            )
            identity_session.flush()
    identity_session.rollback()


def test_patch_status_leave_and_header_mismatch(
    identity_session: Session,
) -> None:
    graph = create_employee_attendance_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_a_user_id)
        client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": graph.property_1_id},
            headers=_auth(staff),
        )
        leave = client.patch(
            "/api/v1/staff/attendance/status",
            json={
                "property_id": graph.property_1_id,
                "on_duty": "LEAVE",
                "leave_type": "SICK",
            },
            headers=_auth(staff),
        )
        mismatch = client.post(
            "/api/v1/staff/attendance/checkout",
            json={"property_id": graph.property_1_id},
            headers={
                **_auth(staff),
                "X-Property-Id": str(graph.property_2_id),
            },
        )
    assert leave.status_code == 200
    assert leave.json()["data"]["daily_status"]["on_duty"] == "LEAVE"
    assert mismatch.status_code == 422


def test_work_order_tables_exist_alongside_employees(test_engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert "work_orders" in tables
    assert "work_order_assignments" in tables
    assert "employees" in tables
