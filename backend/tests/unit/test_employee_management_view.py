"""Scoped employee-management view and department-maintenance tests."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, EmployeeErrorCode
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.employee import Employee, EmployeeProperty
from app.service.employee import EmployeeService


NOW = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _actor() -> Principal:
    return Principal(
        user_id=31,
        status="ACTIVE",
        display_name="property admin",
        grants=(
            RoleGrant(
                role_code="PROPERTY_ADMIN",
                scope=OrganizationScope(
                    ScopeType.PROPERTY,
                    tenant_id=4,
                    property_id=8,
                ),
                permissions=frozenset({"employee:read", "employee:manage"}),
            ),
        ),
    )


def _employee(*, department_id: int | None = 9) -> Employee:
    employee = Employee()
    employee.id = 41
    employee.tenant_id = 4
    employee.employee_no = "SYN-41"
    employee.status = "ACTIVE"
    employee.department_id = department_id
    employee.max_capacity = 3
    return employee


def test_list_employees_enforces_target_property_before_querying() -> None:
    repository = MagicMock()
    employee = _employee()
    assignments = (SimpleNamespace(id=51, property_id=8),)
    duties = (SimpleNamespace(id=61),)
    skills = (SimpleNamespace(id=71),)
    repository.list_current_employees_for_property.return_value = (employee,)
    repository.list_assignments_for_employee.return_value = assignments
    repository.list_duties_for_employee.return_value = duties
    repository.list_skills_for_employee.return_value = skills

    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._clock = MagicMock(return_value=NOW)
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_managed_property = MagicMock(
        return_value=SimpleNamespace(id=8, tenant_id=4)
    )

    result = service.list_employees(
        actor=_actor(),
        tenant_id=4,
        property_id=8,
        after_id=10,
        limit=25,
    )

    service._require_managed_property.assert_called_once_with(
        service._reload_actor.return_value,
        tenant_id=4,
        property_id=8,
        permission="employee:read",
    )
    repository.list_current_employees_for_property.assert_called_once_with(
        tenant_id=4,
        property_id=8,
        now=NOW,
        after_id=10,
        limit=25,
    )
    assert result == ((employee, assignments, duties, skills),)


def test_property_scoped_employee_view_hides_other_property_assignments() -> None:
    employee = _employee()
    visible = EmployeeProperty()
    visible.id = 51
    visible.property_id = 8
    hidden = EmployeeProperty()
    hidden.id = 52
    hidden.property_id = 9

    result = EmployeeService._visible_employee_assignments(
        _actor(),
        employee,
        (visible, hidden),
        permission="employee:read",
    )

    assert result == (visible,)


def test_catalog_returns_only_departments_available_to_target_property() -> None:
    repository = MagicMock()
    departments = (SimpleNamespace(id=2),)
    duties = (SimpleNamespace(id=3),)
    skills = (SimpleNamespace(id=4),)
    repository.list_departments_for_property.return_value = departments
    repository.list_duties_for_tenant.return_value = duties
    repository.list_skills_for_tenant.return_value = skills

    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_managed_property = MagicMock(
        return_value=SimpleNamespace(id=8, tenant_id=4)
    )

    result = service.list_catalog(actor=_actor(), tenant_id=4, property_id=8)

    repository.list_departments_for_property.assert_called_once_with(
        tenant_id=4,
        property_id=8,
    )
    assert result == (departments, duties, skills)


def test_update_employee_can_explicitly_clear_department() -> None:
    employee = _employee()
    repository = MagicMock()
    repository.get_employee.return_value = employee
    repository.list_assignments_for_employee.return_value = ()
    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_employee_permission = MagicMock()
    service._append_audit = MagicMock()

    result = service._update_employee(
        actor=_actor(),
        employee_id=employee.id,
        request_id="clear-department",
        status=None,
        department_id=None,
        max_capacity=None,
        add_duty_ids=None,
        add_skill_ids=None,
        clear_department=True,
    )

    assert result.department_id is None
    assert service._append_audit.call_args.kwargs["details"] == {
        "department_id": None,
        "status": "ACTIVE",
    }


def test_update_employee_rejects_set_and_clear_department_together() -> None:
    employee = _employee()
    repository = MagicMock()
    repository.get_employee.return_value = employee
    repository.list_assignments_for_employee.return_value = ()
    service = EmployeeService.__new__(EmployeeService)
    service._repository = repository
    service._reload_actor = MagicMock(return_value=_actor())
    service._require_employee_permission = MagicMock()

    with pytest.raises(AppError) as captured:
        service._update_employee(
            actor=_actor(),
            employee_id=employee.id,
            request_id="invalid-department-change",
            status=None,
            department_id=12,
            max_capacity=None,
            add_duty_ids=None,
            add_skill_ids=None,
            clear_department=True,
        )

    assert captured.value.code == EmployeeErrorCode.INVALID_INPUT
    assert employee.department_id == 9


def test_miniprogram_staff_management_wires_existing_business_apis() -> None:
    page = (
        REPOSITORY_ROOT / "miniprogram" / "pages" / "supervisor" / "staff.js"
    ).read_text(encoding="utf-8")
    api = (REPOSITORY_ROOT / "miniprogram" / "services" / "api.js").read_text(
        encoding="utf-8"
    )

    assert "employee:manage" in page
    assert ".createEmployee" in page
    assert ".issueEmployeeInvite" in page
    assert ".assignEmployeeProperty" in page
    assert ".endEmployeeProperty" in page
    assert "api.staffDaily" in page
    assert 'path: `/admin/employees?property_id=${propertyId}&tenant_id=${tenantId}`' in api
    assert 'path: "/admin/employees", method: "POST"' in api
