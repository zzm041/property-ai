"""Gate A ORM metadata tests without creating database tables."""

from sqlalchemy import BigInteger

from app.model import Base
from app.model.identity import AuditLog, Permission, Role, RolePermission, User, UserRole

TASK_3_TABLES = {
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "audit_logs",
}


def test_gate_a_metadata_contains_exact_task_3_tables() -> None:
    task_2_tables = {"tenants", "properties", "buildings", "units", "houses"}

    assert set(Base.metadata.tables) == task_2_tables | TASK_3_TABLES


def test_gate_a_models_use_bigint_auto_increment_primary_keys() -> None:
    for model in (User, Role, Permission, RolePermission, UserRole, AuditLog):
        primary_key = model.__table__.c.id
        assert isinstance(primary_key.type, BigInteger)
        assert primary_key.primary_key is True
        assert primary_key.autoincrement is True


def test_role_relations_reference_role_id_not_role_code() -> None:
    role_permission_columns = set(RolePermission.__table__.c.keys())
    user_role_columns = set(UserRole.__table__.c.keys())

    assert "role_id" in role_permission_columns
    assert "role_id" in user_role_columns
    assert "role_code" not in role_permission_columns
    assert "role_code" not in user_role_columns

    role_targets = {
        element.target_fullname
        for table in (RolePermission.__table__, UserRole.__table__)
        for constraint in table.foreign_key_constraints
        for element in constraint.elements
        if element.parent.name == "role_id"
    }
    assert role_targets == {"roles.id"}


def test_user_role_generated_columns_are_stored_and_database_computed() -> None:
    expected_fragments = {
        "system_scope_marker": "SCOPE_TYPE = 'SYSTEM'",
        "tenant_scope_key": "SCOPE_TYPE = 'TENANT'",
        "property_scope_key": "SCOPE_TYPE = 'PROPERTY'",
    }

    for column_name, fragment in expected_fragments.items():
        column = UserRole.__table__.c[column_name]
        assert column.computed is not None
        assert column.computed.persisted is True
        assert fragment in str(column.computed.sqltext).upper()


def test_user_role_unique_and_check_constraints_are_named() -> None:
    unique_names = {
        constraint.name
        for constraint in UserRole.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    check_names = {
        constraint.name
        for constraint in UserRole.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert unique_names == {
        "uq_user_roles_system_grant",
        "uq_user_roles_tenant_grant",
        "uq_user_roles_property_grant",
    }
    assert check_names == {"ck_user_roles_scope_shape"}


def test_audit_log_is_append_only_shape() -> None:
    columns = set(AuditLog.__table__.c.keys())

    assert {
        "actor_user_id",
        "tenant_id",
        "property_id",
        "action",
        "resource_type",
        "resource_id",
        "request_id",
        "created_at",
    } <= columns
    assert "updated_at" not in columns
