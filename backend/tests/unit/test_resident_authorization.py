"""Task 4 source-level authorization and schema contract tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.identity import ScopeType
from app.schema.resident import CreateResidentBindRequest


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_scope_enum_does_not_include_self() -> None:
    assert {item.value for item in ScopeType} == {"SYSTEM", "TENANT", "PROPERTY"}


def test_create_schema_forbids_client_user_id() -> None:
    with pytest.raises(ValidationError):
        CreateResidentBindRequest.model_validate(
            {
                "house_id": 1,
                "relation_type": "OWNER",
                "user_id": 99,
            }
        )


def test_review_authorization_is_permission_and_ownership_based() -> None:
    service = (BACKEND_ROOT / "app" / "service" / "identity" / "resident.py").read_text(
        encoding="utf-8"
    )
    repository = (BACKEND_ROOT / "app" / "repository" / "resident.py").read_text(
        encoding="utf-8"
    )
    # The IAM domain is a package now: keep reading the whole domain, not one file.
    domain = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((BACKEND_ROOT / "app" / "domain" / "identity").glob("*.py"))
    )

    assert "PROPERTY_ADMIN" not in service
    assert "TENANT_ADMIN" not in service
    assert "role ==" not in service
    assert "require_permission" in service
    assert "SELF" not in service
    assert "get_house_by_id" not in repository
    assert "SELF" not in domain
    assert "delete(" not in repository
    assert "end_relation" in repository
    assert ".commit(" not in repository
    assert ".commit(" not in service


def test_seed_review_permissions_follow_frozen_matrix() -> None:
    from app.domain.dev import SEED_ROLE_PERMISSIONS

    assert "resident_binding:review" not in SEED_ROLE_PERMISSIONS["SYSTEM_ADMIN"]
    assert "resident_binding:review" in SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"]
    assert "resident_binding:review" in SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]
    assert "resident_binding:create" in SEED_ROLE_PERMISSIONS["RESIDENT"]
    assert "resident_binding:create" not in SEED_ROLE_PERMISSIONS["STAFF"]
