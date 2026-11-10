"""PA-134: the manual-dispatch 409 must not be a cross-tenant existence oracle.

PA-127 F1 gave every dispatch denial a machine-readable ``details.reason``. That
made the manual path honest, but the two pre-checks in ``_assign`` still answered
different reasons for "no such employee" (``EMPLOYEE_NOT_FOUND``) and "employee
exists, but in another tenant" (``TENANT_MISMATCH``) — so an operator could walk
employee ids and learn which ids exist outside their own tenant.

The fix keeps the reason for internal diagnostics only:

* ``_assign`` loads the assignee through the tenant-scoped locking read, so a
  foreign row is never even materialised;
* both cases answer the identical ``EMPLOYEE_NOT_FOUND`` envelope;
* the distinction survives in the server log (``dispatch_employee_lookup_missed``
  with ``error_reason=EMPLOYEE_FOREIGN`` vs ``EMPLOYEE_NOT_FOUND``);
* ``_reopen`` (which loads the assignee itself) still reports ``TENANT_MISMATCH``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError, WorkOrderErrorCode
from app.core.exception_handlers import install_exception_handlers
from app.service.work_order import WorkOrderService, _employee_scope_denial

TENANT = 5
FOREIGN_TENANT = 9
EMPLOYEE_ID = 17
WORK_ORDER_ID = 41


def _employee(tenant_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=EMPLOYEE_ID,
        tenant_id=tenant_id,
        status="ACTIVE",
        user_id=71,
        max_capacity=3,
    )


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        id=WORK_ORDER_ID,
        tenant_id=TENANT,
        property_id=9,
        status="PENDING_ASSIGNMENT",
        version=3,
    )


def _client() -> TestClient:
    """Two routes that raise the two denials, so the wire bodies can be compared."""

    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/unknown-id")
    def _unknown() -> None:
        raise _employee_scope_denial(None, tenant_id=TENANT)

    @app.get("/foreign-id")
    def _foreign() -> None:
        raise _employee_scope_denial(_employee(FOREIGN_TENANT), tenant_id=TENANT)

    return TestClient(app, raise_server_exceptions=False)


def test_cross_tenant_and_unknown_ids_return_the_same_envelope() -> None:
    client = _client()

    unknown = client.get("/unknown-id")
    foreign = client.get("/foreign-id")

    assert unknown.status_code == foreign.status_code == 409
    assert unknown.json()["error"] == foreign.json()["error"], (
        "a differing body would resurrect the cross-tenant existence oracle"
    )
    assert unknown.json()["error"] == {
        "code": WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE,
        "message": "Employee is not eligible for dispatch",
        "details": {"reason": "EMPLOYEE_NOT_FOUND"},
    }


def test_assign_loads_the_assignee_through_the_tenant_scoped_locking_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    service = WorkOrderService.__new__(WorkOrderService)
    service._reload_actor = lambda actor: actor  # type: ignore[method-assign]
    service._lock_work_order = lambda work_order_id: record  # type: ignore[method-assign]
    service._assert_version = lambda locked, version: None  # type: ignore[method-assign]
    service._property_scope = lambda locked: None  # type: ignore[method-assign]
    employees = SimpleNamespace(
        calls=[],
        get_employee_in_tenant=lambda **kwargs: employees.calls.append(kwargs) or None,
        get_employee=lambda employee_id: _employee(FOREIGN_TENANT),
    )
    service._employees = employees
    monkeypatch.setattr(
        "app.service.work_order.RbacService.require_permission",
        lambda *args, **kwargs: None,
    )
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "app.service.work_order.logger.warning",
        lambda event, **fields: logged.append((event, fields)),
    )

    with pytest.raises(AppError) as denied:
        service._assign(
            actor=SimpleNamespace(user_id=1, tenant_id=TENANT),
            work_order_id=WORK_ORDER_ID,
            employee_id=EMPLOYEE_ID,
            request_id="req-pa134",
            reason="manual",
            version=3,
            claimed_tenant_id=None,
            claimed_property_id=None,
        )

    assert denied.value.details == {"reason": "EMPLOYEE_NOT_FOUND"}
    assert employees.calls == [
        {"tenant_id": TENANT, "employee_id": EMPLOYEE_ID, "for_update": True}
    ], "the tenant predicate has to travel with the locking read"
    assert logged == [
        (
            "dispatch_employee_lookup_missed",
            {
                "employee_id": EMPLOYEE_ID,
                "tenant_id": TENANT,
                "error_reason": "EMPLOYEE_FOREIGN",
            },
        )
    ]


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (lambda employee_id: None, "EMPLOYEE_NOT_FOUND"),
        (lambda employee_id: _employee(FOREIGN_TENANT), "EMPLOYEE_FOREIGN"),
    ],
)
def test_the_log_still_tells_operations_which_case_it_was(
    monkeypatch: pytest.MonkeyPatch, probe, expected: str
) -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._employees = SimpleNamespace(get_employee=probe)
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "app.service.work_order.logger.warning",
        lambda event, **fields: logged.append((event, fields)),
    )

    service._log_assignment_lookup_missed(EMPLOYEE_ID, tenant_id=TENANT)

    assert logged[0][1]["error_reason"] == expected
    assert logged[0][1]["employee_id"] == EMPLOYEE_ID


def test_reopen_keeps_reporting_tenant_mismatch() -> None:
    """``_reopen`` loads the assignee itself, so the foreign row still reaches
    the roster gate and must keep its own reason."""

    service = WorkOrderService.__new__(WorkOrderService)

    denial = service._eligibility_error(_record(), _employee(FOREIGN_TENANT))

    assert denial is not None
    assert denial.details == {"reason": "TENANT_MISMATCH"}


def test_assign_no_longer_uses_the_tenant_agnostic_locking_read() -> None:
    import inspect

    source = inspect.getsource(WorkOrderService._assign)

    assert "get_employee_in_tenant" in source
    assert "get_employee_for_update" not in source
    assert 'reason="TENANT_MISMATCH"' not in inspect.getsource(
        _employee_scope_denial
    )
