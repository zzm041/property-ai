"""WorkOrder permission matrix: no SELF scope, SYSTEM_ADMIN not auto-granted."""

from app.domain.dev import SEED_ROLE_PERMISSIONS
from app.domain.identity import ScopeType
from app.domain.work_order import (
    WORK_ORDER_CANCEL,
    WORK_ORDER_CREATE,
    WORK_ORDER_DECIDE,
    WORK_ORDER_DISPATCH,
    WORK_ORDER_PROCESS,
    WORK_ORDER_READ,
    WORK_ORDER_RESOLVE,
)


def test_system_admin_has_no_work_order_business_permissions() -> None:
    system = set(SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"])
    assert WORK_ORDER_CREATE not in system
    assert WORK_ORDER_READ not in system
    assert WORK_ORDER_DISPATCH not in system
    assert WORK_ORDER_PROCESS not in system
    assert WORK_ORDER_RESOLVE not in system
    assert WORK_ORDER_DECIDE not in system
    assert WORK_ORDER_CANCEL not in system


def test_resident_create_is_not_staff_privilege() -> None:
    assert WORK_ORDER_CREATE in SEED_ROLE_PERMISSIONS["RESIDENT"]
    assert WORK_ORDER_CREATE not in SEED_ROLE_PERMISSIONS["STAFF"]
    assert WORK_ORDER_DISPATCH not in SEED_ROLE_PERMISSIONS["STAFF"]
    assert WORK_ORDER_DECIDE not in SEED_ROLE_PERMISSIONS["STAFF"]


def test_supervisor_and_property_admin_can_dispatch() -> None:
    for role in ("SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"):
        granted = set(SEED_ROLE_PERMISSIONS[role])
        assert WORK_ORDER_DISPATCH in granted
        assert WORK_ORDER_DECIDE in granted
        assert WORK_ORDER_READ in granted


def test_self_is_not_an_organization_scope() -> None:
    assert "SELF" not in {item.value for item in ScopeType}
