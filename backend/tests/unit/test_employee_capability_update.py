"""Add-only employee capability idempotency and audit-delta tests."""

from unittest.mock import MagicMock

from sqlalchemy.exc import IntegrityError

from app.domain.identity import Principal
from app.model.employee import Duty, Employee
from app.service.employee import EmployeeService


def _employee() -> Employee:
    employee = Employee()
    employee.id = 7
    employee.tenant_id = 1
    employee.status = "ACTIVE"
    employee.department_id = None
    employee.max_capacity = 3
    return employee


def _actor() -> Principal:
    return Principal(
        user_id=2,
        status="ACTIVE",
        display_name="property admin",
        grants=(),
    )


def test_existing_capability_link_is_an_idempotent_noop() -> None:
    session = MagicMock()
    repository = MagicMock()
    duty = Duty()
    duty.id = 5
    repository.get_duty_in_tenant.return_value = duty
    repository.has_employee_duty.return_value = True
    service = EmployeeService.__new__(EmployeeService)
    service._session = session
    service._repository = repository

    added_duties, added_skills = service._link_duties_and_skills(
        _employee(),
        duty_ids=(5,),
        skill_ids=(),
    )

    assert added_duties == ()
    assert added_skills == ()
    repository.add_employee_duty.assert_not_called()
    session.begin_nested.assert_not_called()


def test_concurrent_duplicate_capability_link_is_an_idempotent_noop() -> None:
    session = MagicMock()
    nested = MagicMock()
    session.begin_nested.return_value = nested
    repository = MagicMock()
    duty = Duty()
    duty.id = 5
    repository.get_duty_in_tenant.return_value = duty
    repository.has_employee_duty.return_value = False
    repository.add_employee_duty.side_effect = IntegrityError(
        "insert",
        {},
        Exception(1062, "Duplicate entry for key 'uq_employee_duties_employee_duty'"),
    )
    service = EmployeeService.__new__(EmployeeService)
    service._session = session
    service._repository = repository

    added_duties, added_skills = service._link_duties_and_skills(
        _employee(),
        duty_ids=(5,),
        skill_ids=(),
    )

    assert added_duties == ()
    assert added_skills == ()
    nested.__enter__.assert_called_once_with()
    nested.__exit__.assert_called_once()


def test_repeated_capability_update_does_not_write_false_audit_delta() -> None:
    employee = _employee()
    repository = MagicMock()
    repository.get_employee_for_update.return_value = employee
    repository.list_assignments_for_employee.return_value = ()
    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_employee_permission = MagicMock()
    service._add_duties = MagicMock(return_value=())
    service._add_skills = MagicMock(return_value=())
    service._append_audit = MagicMock()

    result = service._update_employee(
        actor=_actor(),
        employee_id=employee.id,
        request_id="employee-capability-replay",
        status=None,
        department_id=None,
        max_capacity=None,
        add_duty_ids=(5,),
        add_skill_ids=(6,),
    )

    assert result is employee
    service._append_audit.assert_not_called()


def test_capability_update_audits_only_links_inserted_by_this_request() -> None:
    employee = _employee()
    repository = MagicMock()
    repository.get_employee_for_update.return_value = employee
    repository.list_assignments_for_employee.return_value = ()
    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_employee_permission = MagicMock()
    service._add_duties = MagicMock(return_value=(5,))
    service._add_skills = MagicMock(return_value=(6,))
    service._append_audit = MagicMock()

    service._update_employee(
        actor=_actor(),
        employee_id=employee.id,
        request_id="employee-capability-add",
        status=None,
        department_id=None,
        max_capacity=None,
        add_duty_ids=(5, 8),
        add_skill_ids=(6, 9),
    )

    details = service._append_audit.call_args.kwargs["details"]
    assert details == {
        "status": "ACTIVE",
        "added_duty_ids": [5],
        "added_skill_ids": [6],
    }
