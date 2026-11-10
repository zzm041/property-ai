"""Live work-order access/dispatch contracts; synthetic repositories only."""

from datetime import UTC, date, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, WorkOrderErrorCode
from app.domain.work_order import WORK_ORDER_READ
from app.service.work_order import WorkOrderService


NOW = datetime(2026, 11, 1, 18, tzinfo=UTC)


@pytest.fixture
def ctx():
    service = WorkOrderService.__new__(WorkOrderService)
    service._clock = lambda: NOW
    service._employees = MagicMock()
    service._repository = MagicMock()
    service._analysis = MagicMock()
    employee = NS(id=17, tenant_id=4, user_id=8, status="ACTIVE", max_capacity=2)
    record = NS(id=41, tenant_id=4, property_id=9, reporter_user_id=7,
                status="PENDING_ASSIGNMENT", version=1)
    actor = MagicMock(user_id=8)
    actor.has_permission.side_effect = lambda permission, scope: permission == WORK_ORDER_READ
    service._reload_actor = MagicMock(return_value=actor)
    service._employees.get_employee_for_user.return_value = employee
    service._employees.get_employee_for_update.return_value = employee
    service._employees.find_effective_assignment.return_value = object()
    service._employees.get_daily_status_for_update.return_value = NS(on_duty="ON_DUTY")
    service._repository.get_work_order.return_value = record
    service._repository.get_active_assignment.return_value = NS(employee_id=17)
    service._repository.get_property.return_value = NS(timezone="Asia/Shanghai")
    service._repository.employee_global_occupancy.return_value = 0
    service._analysis.get_user_status.return_value = "ACTIVE"
    service._analysis.list_employee_role_codes.return_value = frozenset({"STAFF"})
    service._analysis.get_attendance.return_value = NS(check_in_at=NOW, check_out_at=None)
    return NS(service=service, actor=actor, record=record, employee=employee)


@pytest.mark.parametrize("status", [None, "DISABLED", "LOCKED"])
def test_manual_dispatch_rejects_inactive_linked_account(ctx, status):
    ctx.service._analysis.get_user_status.return_value = status
    error = ctx.service._eligibility_error(ctx.record, ctx.employee)
    assert error is not None and error.code == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE


def test_manual_dispatch_rejects_unlinked_employee(ctx):
    ctx.employee.user_id = None
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None


@pytest.mark.parametrize("roles", [frozenset(), frozenset({"RESIDENT"}), frozenset({"SYSTEM_ADMIN"})])
def test_manual_dispatch_rejects_missing_scoped_staff_role(ctx, roles):
    ctx.service._analysis.list_employee_role_codes.return_value = roles
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None


@pytest.mark.parametrize("role", ["STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"])
def test_valid_roles_are_checked_in_target_scope_and_local_day(ctx, role):
    ctx.service._analysis.list_employee_role_codes.return_value = frozenset({role})
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is None
    ctx.service._analysis.list_employee_role_codes.assert_called_once_with(8, tenant_id=4, property_id=9)
    ctx.service._analysis.get_attendance.assert_called_once_with(
        employee_id=17, property_id=9, business_date=date(2026, 11, 2),
    )


@pytest.mark.parametrize("attendance", [None, NS(check_in_at=None, check_out_at=None)])
@pytest.mark.parametrize("ignore_capacity", [False, True])
def test_missing_check_in_is_not_overridden_by_daily_on_duty(ctx, attendance, ignore_capacity):
    ctx.service._analysis.get_attendance.return_value = attendance
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=ignore_capacity) is not None


@pytest.mark.parametrize("failure", ["account", "role", "attendance"])
def test_manual_assign_rejects_before_assignment_or_load_changes(ctx, failure):
    service = ctx.service
    ctx.actor.has_permission.side_effect = lambda *_: True
    service._lock_work_order = MagicMock(return_value=ctx.record)
    service._repository.get_active_assignment.return_value = None
    service._create_assignment = MagicMock(side_effect=AssertionError("Ineligible employee reached assignment creation"))
    service._adjust_load = MagicMock()
    if failure == "account":
        service._analysis.get_user_status.return_value = "DISABLED"
    elif failure == "role":
        service._analysis.list_employee_role_codes.return_value = frozenset()
    else:
        service._analysis.get_attendance.return_value = None
    with pytest.raises(AppError) as error:
        service._assign(actor=ctx.actor, work_order_id=41, employee_id=17,
                        request_id="synthetic-dispatch", reason=None, version=1,
                        claimed_tenant_id=None, claimed_property_id=None)
    assert error.value.code == WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE
    service._create_assignment.assert_not_called()
    service._adjust_load.assert_not_called()


@pytest.mark.parametrize("failure", ["employee_disabled", "other_tenant", "relation_ended"])
@pytest.mark.parametrize("resource", ["detail", "attachment"])
def test_current_assignment_cannot_preserve_revoked_employee_access(ctx, failure, resource):
    service = ctx.service
    if failure == "employee_disabled":
        ctx.employee.status = "DISABLED"
    elif failure == "other_tenant":
        ctx.employee.tenant_id = 5
    else:
        service._employees.find_effective_assignment.return_value = None
    service._repository.get_file.return_value = NS(id=22, uploader_user_id=99)
    service._repository.list_work_orders_for_file.return_value = (ctx.record,)
    service._read_stored_file = MagicMock(return_value=b"synthetic-private-content")
    with pytest.raises(AppError) as error:
        if resource == "detail":
            service.get_work_order_detail(actor=ctx.actor, work_order_id=41)
        else:
            service.get_file_content(actor=ctx.actor, file_id=22)
    assert error.value.status_code == (403 if resource == "detail" else 404)
    service._read_stored_file.assert_not_called()
    service._repository.get_content.assert_not_called()


def test_active_assignee_keeps_read_access_without_attendance_requirement(ctx):
    ctx.service._employees.get_daily_status_for_update.return_value = None
    ctx.service._analysis.get_attendance.return_value = None
    assert ctx.service.get_work_order(actor=ctx.actor, work_order_id=41) is ctx.record
    ctx.service._employees.find_effective_assignment.assert_called_once_with(
        employee_id=17, property_id=9, now=NOW,
    )


@pytest.mark.parametrize("viewer", ["reporter", "manager"])
def test_reporter_and_scoped_manager_do_not_require_employee_relationship(ctx, viewer):
    ctx.service._employees.get_employee_for_user.return_value = None
    if viewer == "reporter":
        ctx.actor.user_id = 7
    else:
        ctx.actor.has_permission.side_effect = lambda *_: True
    assert ctx.service.get_work_order(actor=ctx.actor, work_order_id=41) is ctx.record


def test_capacity_exception_only_bypasses_capacity(ctx):
    ctx.service._repository.employee_global_occupancy.return_value = 99
    assert ctx.service._eligibility_error(ctx.record, ctx.employee) is not None
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=True) is None
    ctx.service._analysis.get_user_status.return_value = "DISABLED"
    assert ctx.service._eligibility_error(ctx.record, ctx.employee, ignore_capacity=True) is not None


def test_disabled_staff_cannot_list_old_assignments(ctx):
    ctx.employee.status = "DISABLED"
    with pytest.raises(AppError) as error:
        ctx.service.list_staff_work_orders(actor=ctx.actor)
    assert error.value.status_code == 403
    ctx.service._repository.list_for_assignee.assert_not_called()


def test_staff_list_intersects_effective_properties_and_current_read_scope(ctx):
    service = ctx.service
    service._employees.list_active_property_ids_for_employee.return_value = (9, 10)
    ctx.actor.has_permission.side_effect = lambda permission, scope: (
        permission == WORK_ORDER_READ and scope.tenant_id == 4 and scope.property_id == 9
    )
    service._repository.list_for_assignee.return_value = (ctx.record,)
    assert service.list_staff_work_orders(actor=ctx.actor, after_id=20, limit=1) == (ctx.record,)
    service._employees.list_active_property_ids_for_employee.assert_called_once_with(employee_id=17, now=NOW)
    service._repository.list_for_assignee.assert_called_once_with(
        employee_id=17, tenant_id=4, property_ids=(9,), after_id=20, limit=1,
    )


@pytest.mark.parametrize("has_relation", [False, True])
def test_staff_list_without_effective_read_scope_does_not_query_orders(ctx, has_relation):
    ctx.service._employees.list_active_property_ids_for_employee.return_value = (9,) if has_relation else ()
    ctx.actor.has_permission.side_effect = lambda *_: False
    assert ctx.service.list_staff_work_orders(actor=ctx.actor) == ()
    ctx.service._repository.list_for_assignee.assert_not_called()


def test_staff_repository_filters_scope_before_pagination():
    from sqlalchemy.dialects import mysql
    from app.repository.work_order import WorkOrderRepository

    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = WorkOrderRepository(session)
    assert repository.list_for_assignee(employee_id=17, tenant_id=4, property_ids=(9,), after_id=20, limit=1) == ()
    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    where = sql.split("WHERE", 1)[1]
    assert "work_orders.tenant_id = 4" in where
    assert "work_orders.property_id IN (9)" in where
    assert "work_order_assignments.employee_id = 17" in where
    assert "work_orders.id > 20" in where
    assert where.index("work_orders.property_id") < where.index("LIMIT 1")
    session.reset_mock()
    assert repository.list_for_assignee(employee_id=17, tenant_id=4, property_ids=()) == ()
    session.scalars.assert_not_called()
