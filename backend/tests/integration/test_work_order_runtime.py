"""WorkOrder create, FSM, dispatch, processing, confirmation, security, concurrency."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, IamErrorCode, WorkOrderErrorCode
from app.core.time import utc_now
from app.domain.audit import AuditAction
from app.main import create_app
from app.model.identity import AuditLog
from app.model.work_order import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderLog,
)
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.employee import EmployeeService
from app.service.work_order import WorkOrderService
from tests.integration.conftest import commit_open_transaction
from tests.integration.work_order_test_data import create_work_order_graph

TEST_SECRET = "t6-work-order-secret-that-is-not-production"


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
        headers={"X-Request-Id": f"t6-login-{user_id}"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _auth(token: str, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-Id"] = request_id
    return headers


def _principal(session: Session, user_id: int):
    principal = AuthService(session, IdentityRepository(session)).load_principal(user_id)
    commit_open_transaction(session)
    return principal


def _check_in(session: Session, user_id: int, property_id: int, request_id: str) -> None:
    EmployeeService(session).check_in(
        actor=_principal(session, user_id),
        property_id=property_id,
        request_id=request_id,
    )


def _idempotency() -> dict[str, str]:
    return {"Idempotency-Key": f"t6-{uuid4()}"}


def test_resident_creates_own_house_work_order(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.employees.resident_id)
        created = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Broken tap",
                "category": "PLUMBING",
                "priority": "HIGH",
            },
            headers={**_auth(token, "t6-create"), **_idempotency()},
        )
        forbidden_creator = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Forged",
                "creator_user_id": graph.employees.manager_p1_id,
            },
            headers={**_auth(token, "t6-create-forged"), **_idempotency()},
        )
        other_house = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_2_id, "text": "Not mine"},
            headers={**_auth(token, "t6-create-other"), **_idempotency()},
        )
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "PENDING_ANALYSIS"
    assert created.json()["data"]["reporter_user_id"] == graph.employees.resident_id
    assert created.json()["data"]["house_id"] == graph.house_1_id
    assert forbidden_creator.status_code == 422
    assert other_house.status_code == 403
    assert other_house.json()["error"]["code"] == WorkOrderErrorCode.HOUSE_NOT_OWNED
    audits = identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "t6-create")
    ).all()
    assert [item.action for item in audits] == [AuditAction.WORK_ORDER_CREATED.value]


def test_idempotent_create_returns_same_work_order(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    key = {"Idempotency-Key": "same-key-once"}
    with _client() as client:
        token = _token(client, graph.employees.resident_id)
        first = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Same"},
            headers={**_auth(token, "t6-idemp-1"), **key},
        )
        second = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Same"},
            headers={**_auth(token, "t6-idemp-2"), **key},
        )
        conflict = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Different"},
            headers={**_auth(token, "t6-idemp-3"), **key},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == WorkOrderErrorCode.IDEMPOTENCY_CONFLICT


def test_cross_property_house_claim_is_rejected(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.employees.resident_id)
        mismatch = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Leak",
                "property_id": graph.employees.property_2_id,
            },
            headers={**_auth(token, "t6-scope"), **_idempotency()},
        )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == WorkOrderErrorCode.SCOPE_MISMATCH


def test_manual_dispatch_and_staff_loop(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-checkin-a",
    )
    with _client() as client:
        resident = _token(client, graph.employees.resident_id)
        manager = _token(client, graph.employees.manager_p1_id)
        staff = _token(client, graph.employees.staff_a_user_id)
        staff_b = _token(client, graph.employees.staff_b_user_id)
        created = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Outlet spark", "priority": "URGENT"},
            headers={**_auth(resident, "t6-loop-create"), **_idempotency()},
        )
        work_order_id = created.json()["data"]["id"]
        inactive = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_disabled_id},
            headers=_auth(manager, "t6-assign-disabled"),
        )
        off_duty = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_b_id},
            headers=_auth(manager, "t6-assign-offduty"),
        )
        cross = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_p2_id},
            headers=_auth(manager, "t6-assign-p2"),
        )
        assigned = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_a_id},
            headers=_auth(manager, "t6-assign"),
        )
        wrong_staff = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff_b, "t6-accept-wrong"),
        )
        accepted = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff, "t6-accept"),
        )
        started = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff, "t6-start"),
        )
        file_resp = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 8,
                "property_id": graph.employees.property_1_id,
            },
            headers=_auth(staff, "t6-file"),
        )
        file_id = file_resp.json()["data"]["id"]
        uploaded = client.post(
            f"/api/v1/files/{file_id}/content",
            files={"file": ("feedback.jpg", b"\xff\xd8\xff\xe0DATA", "image/jpeg")},
            headers=_auth(staff, "t6-file-content"),
        )
        assert uploaded.status_code == 200
        feedback = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/feedback",
            json={"text": "Opened panel", "file_ids": [file_id], "taken_phase": "BEFORE"},
            headers=_auth(staff, "t6-feedback"),
        )
        waiting = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/escalate",
            json={"process_reason": "WAITING_PARTS", "reason": "Need fuse"},
            headers=_auth(staff, "t6-escalate"),
        )
        blocked = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "ELECTRICAL",
                "method": "Replace",
                "resolved": True,
            },
            headers=_auth(staff, "t6-resolve-blocked"),
        )
        decided = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/decide",
            json={"decision": "CONTINUE", "reason": "Parts have arrived"},
            headers=_auth(manager, "t6-decide"),
        )
        cleared = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/feedback",
            json={"process_reason": "NONE", "text": "Parts arrived"},
            headers=_auth(staff, "t6-clear"),
        )
        resolved = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "ELECTRICAL",
                "method": "Replace breaker",
                "resolved": True,
            },
            headers=_auth(staff, "t6-resolve"),
        )
        other_resident = _token(client, graph.resident_b_id)
        stolen_confirm = client.post(
            f"/api/v1/work-orders/{work_order_id}/confirm",
            json={},
            headers=_auth(other_resident, "t6-confirm-stolen"),
        )
        reopened = client.post(
            f"/api/v1/work-orders/{work_order_id}/reopen",
            json={"reason": "Still sparks"},
            headers=_auth(resident, "t6-reopen"),
        )
    assert inactive.status_code == 409
    assert inactive.json()["error"]["code"] == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE
    assert off_duty.status_code == 409
    assert cross.status_code == 409
    assert assigned.status_code == 200
    assert assigned.json()["data"]["status"] == "ASSIGNED"
    assert wrong_staff.status_code == 403
    assert wrong_staff.json()["error"]["code"] == WorkOrderErrorCode.NOT_ASSIGNEE
    assert accepted.json()["data"]["status"] == "ACCEPTED"
    assert started.json()["data"]["status"] == "PROCESSING"
    assert "storage_key" not in file_resp.json()["data"]
    assert "scanner_status" not in file_resp.json()["data"]
    assert feedback.status_code == 200
    assert waiting.json()["data"]["process_reason"] == "WAITING_PARTS"
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == WorkOrderErrorCode.INVALID_TRANSITION
    assert decided.status_code == 200
    assert cleared.json()["data"]["process_reason"] == "NONE"
    assert resolved.json()["data"]["status"] == "WAIT_CONFIRM"
    assert stolen_confirm.status_code == 403
    assert reopened.status_code == 200
    assert reopened.json()["data"]["status"] in {"PROCESSING", "PENDING_ASSIGNMENT"}
    logs = identity_session.scalars(
        select(WorkOrderLog).where(WorkOrderLog.work_order_id == work_order_id)
    ).all()
    assert any(item.event_code == "REOPEN" for item in logs)
    assignments = identity_session.scalars(
        select(WorkOrderAssignment).where(
            WorkOrderAssignment.work_order_id == work_order_id
        )
    ).all()
    assert len(assignments) >= 1


def test_reassign_preserves_history(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-reassign-checkin-a",
    )
    _check_in(
        identity_session,
        graph.employees.staff_b_user_id,
        graph.employees.property_1_id,
        "t6-reassign-checkin-b",
    )
    with _client() as client:
        resident = _token(client, graph.employees.resident_id)
        manager = _token(client, graph.employees.manager_p1_id)
        created = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Door lock"},
            headers={**_auth(resident, "t6-re-create"), **_idempotency()},
        )
        work_order_id = created.json()["data"]["id"]
        first = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_a_id},
            headers=_auth(manager, "t6-re-assign-1"),
        )
        second = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={
                "employee_id": graph.employees.employee_b_id,
                "reason": "A is overloaded",
                "version": first.json()["data"]["version"],
            },
            headers=_auth(manager, "t6-re-assign-2"),
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "ASSIGNED"
    rows = list(
        identity_session.scalars(
            select(WorkOrderAssignment)
            .where(WorkOrderAssignment.work_order_id == work_order_id)
            .order_by(WorkOrderAssignment.id)
        )
    )
    assert len(rows) == 2
    assert rows[0].status == "REASSIGNED"
    assert rows[0].ended_at is not None
    assert rows[0].employee_id == graph.employees.employee_a_id
    assert rows[1].status == "ACTIVE"
    assert rows[1].employee_id == graph.employees.employee_b_id


def test_confirm_closes_and_system_admin_is_denied(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-confirm-checkin",
    )
    with _client() as client:
        resident = _token(client, graph.employees.resident_id)
        manager = _token(client, graph.employees.manager_p1_id)
        staff = _token(client, graph.employees.staff_a_user_id)
        system = _token(client, graph.employees.system_admin_id)
        created = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "Light"},
            headers={**_auth(resident, "t6-cf-create"), **_idempotency()},
        )
        work_order_id = created.json()["data"]["id"]
        system_read = client.get(
            f"/api/v1/work-orders/{work_order_id}",
            headers=_auth(system, "t6-cf-system"),
        )
        client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": graph.employees.employee_a_id},
            headers=_auth(manager, "t6-cf-assign"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff, "t6-cf-accept"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff, "t6-cf-start"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "ELECTRICAL",
                "method": "Replace bulb",
                "resolved": True,
            },
            headers=_auth(staff, "t6-cf-resolve"),
        )
        guessed = client.get(
            "/api/v1/work-orders/999999",
            headers=_auth(resident, "t6-cf-guess"),
        )
        confirmed = client.post(
            f"/api/v1/work-orders/{work_order_id}/confirm",
            json={},
            headers=_auth(resident, "t6-cf-confirm"),
        )
        late_start = client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff, "t6-cf-late"),
        )
    assert system_read.status_code == 403
    assert guessed.status_code == 404
    assert guessed.json()["error"]["code"] == WorkOrderErrorCode.NOT_FOUND
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "CLOSED"
    assert late_start.status_code == 409
    assert late_start.json()["error"]["code"] == WorkOrderErrorCode.ALREADY_PROCESSED
    assert "sql" not in late_start.json()["error"]["message"].casefold()


def test_stale_permission_and_cross_tenant_denied(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-stale-checkin",
    )
    with identity_session.begin():
        identity_session.execute(text("DELETE FROM role_permissions"))
    with _client() as client:
        resident = _token(client, graph.employees.resident_id)
        manager = _token(client, graph.employees.manager_p2_id)
        created = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_1_id, "text": "After revoke"},
            headers={**_auth(resident, "t6-stale-create"), **_idempotency()},
        )
        foreign = client.post(
            "/api/v1/work-orders",
            json={"house_id": graph.house_3_id, "text": "Other tenant"},
            headers={**_auth(resident, "t6-stale-t2"), **_idempotency()},
        )
    assert created.status_code == 403
    assert created.json()["error"]["code"] == IamErrorCode.FORBIDDEN
    assert foreign.status_code == 403
    del manager


def test_sla_due_uses_priority_and_utc(identity_session: Session) -> None:
    graph = create_work_order_graph(identity_session)
    before = utc_now()
    with _client() as client:
        token = _token(client, graph.employees.resident_id)
        urgent = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Fire risk",
                "priority": "URGENT",
            },
            headers={**_auth(token, "t6-sla-urgent"), **_idempotency()},
        )
        low = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": graph.house_1_id,
                "text": "Paint chip",
                "priority": "LOW",
            },
            headers={**_auth(token, "t6-sla-low"), **_idempotency()},
        )
    urgent_due = datetime.fromisoformat(urgent.json()["data"]["sla_due_at"])
    low_due = datetime.fromisoformat(low.json()["data"]["sla_due_at"])
    assert urgent_due.tzinfo is not None
    assert (urgent_due - before) < (low_due - before)
    assert urgent.json()["data"]["sla_overdue"] is False


def test_composite_house_fk_rejects_cross_property_row(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    with pytest.raises(Exception):
        identity_session.execute(
            text(
                """
                INSERT INTO work_orders (
                    tenant_id, property_id, house_id, reporter_user_id,
                    status, process_reason, category, severity, priority, version
                ) VALUES (
                    :tenant_id, :property_id, :house_id, :reporter,
                    'PENDING_ANALYSIS', 'NONE', 'OTHER', 'LOW', 'MEDIUM', 1
                )
                """
            ),
            {
                "tenant_id": graph.employees.tenant_1_id,
                "property_id": graph.employees.property_2_id,
                "house_id": graph.house_1_id,
                "reporter": graph.employees.resident_id,
            },
        )
        identity_session.commit()
    identity_session.rollback()


def test_concurrent_first_assign_has_single_winner(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-c1-a",
    )
    _check_in(
        identity_session,
        graph.employees.staff_b_user_id,
        graph.employees.property_1_id,
        "t6-c1-b",
    )
    principal = _principal(identity_session, graph.employees.resident_id)
    created = WorkOrderService(identity_session).create_work_order(
        actor=principal,
        house_id=graph.house_1_id,
        request_id="t6-c1-create",
        idempotency_key=str(uuid4()),
        text="Concurrent assign",
    )
    factory = get_session_factory(integration_settings.test_database_url)

    def attempt(employee_id: int, request_id: str) -> str:
        session = factory()
        try:
            service = WorkOrderService(session)
            try:
                service.assign(
                    actor=_principal(session, graph.employees.manager_p1_id),
                    work_order_id=created.id,
                    employee_id=employee_id,
                    request_id=request_id,
                )
                return "ok"
            except AppError as exc:
                return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, graph.employees.employee_a_id, "t6-c1-a-assign")
        second = pool.submit(attempt, graph.employees.employee_b_id, "t6-c1-b-assign")
        results = {first.result(), second.result()}
    assert "ok" in results
    assert results & {
        WorkOrderErrorCode.ALREADY_ASSIGNED,
        WorkOrderErrorCode.CONFLICT,
        WorkOrderErrorCode.INVALID_TRANSITION,
    }
    rows = identity_session.scalars(
        select(WorkOrderAssignment).where(
            WorkOrderAssignment.work_order_id == created.id,
            WorkOrderAssignment.status == "ACTIVE",
        )
    ).all()
    assert len(rows) == 1


def test_concurrent_confirm_and_reopen_single_winner(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_work_order_graph(identity_session)
    _check_in(
        identity_session,
        graph.employees.staff_a_user_id,
        graph.employees.property_1_id,
        "t6-c2-checkin",
    )
    resident = _principal(identity_session, graph.employees.resident_id)
    manager = _principal(identity_session, graph.employees.manager_p1_id)
    staff = _principal(identity_session, graph.employees.staff_a_user_id)
    service = WorkOrderService(identity_session)
    created = service.create_work_order(
        actor=resident,
        house_id=graph.house_1_id,
        request_id="t6-c2-create",
        idempotency_key=str(uuid4()),
        text="Concurrent receipt",
    )
    service.assign(
        actor=manager,
        work_order_id=created.id,
        employee_id=graph.employees.employee_a_id,
        request_id="t6-c2-assign",
    )
    service.accept(actor=staff, work_order_id=created.id, request_id="t6-c2-accept")
    service.start(actor=staff, work_order_id=created.id, request_id="t6-c2-start")
    service.submit_receipt(
        actor=staff,
        work_order_id=created.id,
        request_id="t6-c2-receipt",
        problem_type="OTHER",
        method="Fixed",
        resolved=True,
    )
    factory = get_session_factory(integration_settings.test_database_url)

    def confirm() -> str:
        session = factory()
        try:
            try:
                WorkOrderService(session).confirm(
                    actor=_principal(session, graph.employees.resident_id),
                    work_order_id=created.id,
                    request_id="t6-c2-confirm",
                )
                return "confirm"
            except AppError as exc:
                return exc.code
        finally:
            session.close()

    def reopen() -> str:
        session = factory()
        try:
            try:
                WorkOrderService(session).reopen(
                    actor=_principal(session, graph.employees.resident_id),
                    work_order_id=created.id,
                    request_id="t6-c2-reopen",
                    reason="No",
                )
                return "reopen"
            except AppError as exc:
                return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = {pool.submit(confirm).result(), pool.submit(reopen).result()}
    assert len(results & {"confirm", "reopen"}) == 1
    identity_session.expire_all()
    final = identity_session.get(WorkOrder, created.id)
    assert final is not None
    assert final.status in {"CLOSED", "PROCESSING", "PENDING_ASSIGNMENT"}
