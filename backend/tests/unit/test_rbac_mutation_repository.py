"""RBAC Mutation Repository query, locking, and transaction discipline tests."""

from unittest.mock import MagicMock

from sqlalchemy.dialects import mysql

from app.domain.identity import OrganizationScope, ScopeType
from app.model.identity import RolePermission, UserRole
from app.repository.rbac_mutation import RbacMutationRepository


def _compiled_sql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_code_lookups_use_binary_exact_collation() -> None:
    session = MagicMock()
    repository = RbacMutationRepository(session)

    repository.get_role_by_code("PROPERTY_ADMIN")
    role_sql = _compiled_sql(session.scalar.call_args.args[0])
    repository.get_permission_by_code("role:manage")
    permission_sql = _compiled_sql(session.scalar.call_args.args[0])

    assert "COLLATE utf8mb4_bin" in role_sql
    assert "'PROPERTY_ADMIN'" in role_sql
    assert "COLLATE utf8mb4_bin" in permission_sql
    assert "'role:manage'" in permission_sql


def test_user_role_lookup_contains_full_scope_identity() -> None:
    session = MagicMock()
    repository = RbacMutationRepository(session)
    scope = OrganizationScope(
        ScopeType.PROPERTY,
        tenant_id=11,
        property_id=22,
    )

    repository.find_user_role(user_id=33, role_id=44, scope=scope)
    sql = _compiled_sql(session.scalar.call_args.args[0])

    assert "user_roles.user_id = 33" in sql
    assert "user_roles.role_id = 44" in sql
    assert "user_roles.scope_type = 'PROPERTY'" in sql
    assert "user_roles.tenant_id = 11" in sql
    assert "user_roles.property_id = 22" in sql
    assert "FOR UPDATE" not in sql


def test_revoke_lookups_use_select_for_update() -> None:
    session = MagicMock()
    repository = RbacMutationRepository(session)

    repository.find_user_role_for_update(
        user_id=1,
        role_id=2,
        scope=OrganizationScope(ScopeType.SYSTEM),
    )
    user_role_sql = _compiled_sql(session.scalar.call_args.args[0])
    repository.find_role_permission_for_update(role_id=3, permission_id=4)
    role_permission_sql = _compiled_sql(session.scalar.call_args.args[0])

    assert "FOR UPDATE" in user_role_sql
    assert "FOR UPDATE" in role_permission_sql


def test_add_delete_flush_without_commit_or_rollback() -> None:
    session = MagicMock()
    repository = RbacMutationRepository(session)
    user_role = UserRole(
        user_id=1,
        role_id=2,
        scope_type="SYSTEM",
    )
    role_permission = RolePermission(role_id=2, permission_id=3)

    repository.add_user_role(user_role)
    repository.delete_user_role(user_role)
    repository.add_role_permission(role_permission)
    repository.delete_role_permission(role_permission)

    assert session.add.call_count == 2
    assert session.delete.call_count == 2
    assert session.flush.call_count == 4
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_repository_has_no_blind_relation_delete_or_transaction_control() -> None:
    forbidden_methods = {
        "delete_user_role_by_id",
        "delete_role_permission_by_id",
        "commit",
        "rollback",
    }

    assert forbidden_methods.isdisjoint(dir(RbacMutationRepository))
