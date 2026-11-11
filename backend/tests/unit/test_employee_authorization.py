"""Task 5 source-level authorization and schema contract tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.dev import SEED_ROLE_PERMISSIONS
from app.domain.identity import ScopeType
from app.schema.employee import AttendanceActionRequest, CreateEmployeeRequest


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_scope_enum_does_not_include_self() -> None:
    assert {item.value for item in ScopeType} == {"SYSTEM", "TENANT", "PROPERTY"}


def test_checkin_schema_forbids_client_employee_id() -> None:
    with pytest.raises(ValidationError):
        AttendanceActionRequest.model_validate(
            {
                "property_id": 1,
                "employee_id": 99,
            }
        )


def test_create_employee_schema_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CreateEmployeeRequest.model_validate(
            {
                "user_id": 1,
                "tenant_id": 1,
                "employee_no": "E1",
                "property_id": 1,
                "password": "secret",
            }
        )


def test_employee_authorization_is_permission_and_ownership_based() -> None:
    service = (BACKEND_ROOT / "app" / "service" / "employee.py").read_text(
        encoding="utf-8"
    )
    repository = (BACKEND_ROOT / "app" / "repository" / "employee.py").read_text(
        encoding="utf-8"
    )
    # The IAM domain is a package now: keep looking at the whole domain, not one file.
    domain = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((BACKEND_ROOT / "app" / "domain" / "identity").glob("*.py"))
    )

    assert "PROPERTY_ADMIN" not in service
    assert "TENANT_ADMIN" not in service
    assert "role ==" not in service
    assert "require_permission" in service
    assert "get_employee_for_user" in service
    assert "ScopeType.SELF" not in service
    assert '"SELF"' not in service
    assert "'SELF'" not in service
    assert "SELF" not in domain
    assert "delete(" not in repository
    assert "end_assignment" in repository
    assert ".commit(" not in repository
    assert ".commit(" not in service
    assert "employee username" not in service.casefold()
    assert "employee password" not in service.casefold()


def test_seed_employee_permissions_follow_frozen_matrix() -> None:
    assert "employee:manage" not in SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"]
    assert "employee:manage" not in SEED_ROLE_PERMISSIONS["STAFF"]
    assert "employee:manage" not in SEED_ROLE_PERMISSIONS["SUPERVISOR"]
    assert "employee:manage" in SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"]
    assert "employee:manage" in SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]
    assert "attendance:check_in" in SEED_ROLE_PERMISSIONS["STAFF"]
    assert "attendance:check_out" in SEED_ROLE_PERMISSIONS["STAFF"]
    assert "attendance:read" not in SEED_ROLE_PERMISSIONS["STAFF"]
    assert "attendance:read" in SEED_ROLE_PERMISSIONS["SUPERVISOR"]
    assert "employee:read" in SEED_ROLE_PERMISSIONS["SUPERVISOR"]
    assert "work_order:create" in SEED_ROLE_PERMISSIONS["RESIDENT"]
    assert "work_order:create" not in SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"]
    assert "work_order:dispatch" not in SEED_ROLE_PERMISSIONS["STAFF"]
    assert "work_order:dispatch" in SEED_ROLE_PERMISSIONS["SUPERVISOR"]
    assert "dispatch:assign" not in {
        code
        for codes in SEED_ROLE_PERMISSIONS.values()
        for code in codes
    }
