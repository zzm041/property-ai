"""Capacity protection and cross-day attendance load consistency."""

from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, WorkOrderErrorCode
from app.core.time import utc_now
from app.repository.identity import IdentityRepository
from app.service.identity.auth import AuthService
from app.service.employee import EmployeeService
from app.service.work_order import WorkOrderService
from tests.integration.conftest import commit_open_transaction
from tests.integration.work_order_test_data import create_work_order_graph


def _principal(session: Session, user_id: int):
    principal = AuthService(session, IdentityRepository(session)).load_principal(user_id)
    commit_open_transaction(session)
    return principal


def test_second_assignment_to_full_employee_is_rejected(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    now = utc_now()
    employees = EmployeeService(identity_session, clock=lambda: now)
    orders = WorkOrderService(identity_session, clock=lambda: now)
    first = orders.create_work_order(
        actor=_principal(identity_session, graph.employees.resident_id),
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-cap-wo-1",
        idempotency_key=str(uuid4()),
        text="First electrical job",
    )
    second = orders.create_work_order(
        actor=_principal(identity_session, graph.employees.resident_id),
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-cap-wo-2",
        idempotency_key=str(uuid4()),
        text="Second electrical job",
    )
    employees.check_in(
        actor=_principal(identity_session, graph.employees.staff_a_user_id),
        property_id=graph.employees.property_1_id,
        request_id="d4-cap-checkin",
    )
    manager = _principal(identity_session, graph.employees.manager_p1_id)
    orders.assign(
        actor=manager,
        work_order_id=first.id,
        employee_id=graph.employees.employee_a_id,
        request_id="d4-cap-assign-1",
    )
    with pytest.raises(AppError) as captured:
        orders.assign(
            actor=manager,
            work_order_id=second.id,
            employee_id=graph.employees.employee_a_id,
            request_id="d4-cap-assign-2",
        )
    assert captured.value.code == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE
    assert captured.value.status_code == 409


def test_cross_day_checkin_preserves_active_assignment_load(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    current = {"now": utc_now()}

    def clock():
        return current["now"]

    employees = EmployeeService(identity_session, clock=clock)
    orders = WorkOrderService(identity_session, clock=clock)
    created = orders.create_work_order(
        actor=_principal(identity_session, graph.employees.resident_id),
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-xd-wo",
        idempotency_key=str(uuid4()),
        text="Overnight assignment",
    )
    staff = _principal(identity_session, graph.employees.staff_a_user_id)
    _, day1 = employees.check_in(
        actor=staff,
        property_id=graph.employees.property_1_id,
        request_id="d4-xd-checkin-1",
    )
    orders.assign(
        actor=_principal(identity_session, graph.employees.manager_p1_id),
        work_order_id=created.id,
        employee_id=graph.employees.employee_a_id,
        request_id="d4-xd-assign",
    )
    employees.check_out(
        actor=staff,
        property_id=graph.employees.property_1_id,
        request_id="d4-xd-checkout",
    )
    current["now"] = current["now"] + timedelta(days=1)
    _, day2 = employees.check_in(
        actor=staff,
        property_id=graph.employees.property_1_id,
        request_id="d4-xd-checkin-2",
    )
    assert day1.business_date != day2.business_date
    assert day2.current_load == 1


def test_concurrent_assign_to_same_employee_respects_max_capacity(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_work_order_graph(identity_session)
    now = utc_now()
    employees = EmployeeService(identity_session, clock=lambda: now)
    orders = WorkOrderService(identity_session, clock=lambda: now)
    first = orders.create_work_order(
        actor=_principal(identity_session, graph.employees.resident_id),
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-cap-race-wo-1",
        idempotency_key=str(uuid4()),
        text="Concurrent electrical job one",
    )
    second = orders.create_work_order(
        actor=_principal(identity_session, graph.employees.resident_id),
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-cap-race-wo-2",
        idempotency_key=str(uuid4()),
        text="Concurrent electrical job two",
    )
    employees.check_in(
        actor=_principal(identity_session, graph.employees.staff_a_user_id),
        property_id=graph.employees.property_1_id,
        request_id="d4-cap-race-checkin",
    )
    commit_open_transaction(identity_session)

    def worker(work_order_id: int) -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            actor = _principal(session, graph.employees.manager_p1_id)
            commit_open_transaction(session)
            service = WorkOrderService(session, clock=lambda: now)
            service.assign(
                actor=actor,
                work_order_id=work_order_id,
                employee_id=graph.employees.employee_a_id,
                request_id=f"d4-cap-race-assign-{work_order_id}",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (first.id, second.id)))
    assert results.count("ok") == 1, results
    assert set(results) <= {
        "ok",
        WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE,
        WorkOrderErrorCode.CONFLICT,
        WorkOrderErrorCode.ASSIGNMENT_CONFLICT,
    }

