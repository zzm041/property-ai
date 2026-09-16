"""Gate A schema inspection against real MySQL 8."""

from sqlalchemy import BigInteger, Engine, inspect, text

TASK_2_TABLES = {"tenants", "properties", "buildings", "units", "houses"}
TASK_3_TABLES = {
    "users",
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "audit_logs",
}


TASK_4_TABLES = {
    "resident_bind_requests",
    "resident_house_relations",
}
TASK_5_TABLES = {
    "departments",
    "duties",
    "skills",
    "employees",
    "employee_properties",
    "employee_duties",
    "employee_skills",
    "attendance_records",
    "employee_daily_statuses",
}


TASK_6_TABLES = {
    "file_objects",
    "sla_policies",
    "work_orders",
    "work_order_contents",
    "work_order_media",
    "work_order_assignments",
    "work_order_logs",
    "work_order_processes",
    "work_order_receipts",
    "work_order_feedbacks",
    "sla_clocks",
    "idempotency_records",
}


def test_gate_a_creates_exact_business_tables(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())

    assert tables == TASK_2_TABLES | TASK_3_TABLES | TASK_4_TABLES | TASK_5_TABLES | TASK_6_TABLES | {
        "alembic_version"
    }


def test_gate_a_primary_and_foreign_key_columns_are_bigint(
    test_engine: Engine,
) -> None:
    inspector = inspect(test_engine)
    foreign_key_columns = {
        "role_permissions": {"role_id", "permission_id"},
        "user_roles": {"user_id", "role_id", "tenant_id", "property_id"},
        "audit_logs": {"actor_user_id", "tenant_id", "property_id"},
    }

    for table in TASK_3_TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert isinstance(columns["id"]["type"], BigInteger)
        assert columns["id"]["autoincrement"] is True
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
        for column_name in foreign_key_columns.get(table, set()):
            assert isinstance(columns[column_name]["type"], BigInteger)


def test_gate_a_foreign_keys_are_named_and_scoped(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected = {
        "role_permissions": {
            "fk_role_permissions_permission_id_permissions",
            "fk_role_permissions_role_id_roles",
        },
        "user_roles": {
            "fk_user_roles_property_tenant_properties",
            "fk_user_roles_role_id_roles",
            "fk_user_roles_tenant_id_tenants",
            "fk_user_roles_user_id_users",
        },
        "audit_logs": {
            "fk_audit_logs_actor_user_id_users",
            "fk_audit_logs_property_tenant_properties",
            "fk_audit_logs_tenant_id_tenants",
        },
    }

    for table, names in expected.items():
        actual = {item["name"] for item in inspector.get_foreign_keys(table)}
        assert actual == names

    user_role_property = next(
        item
        for item in inspector.get_foreign_keys("user_roles")
        if item["name"] == "fk_user_roles_property_tenant_properties"
    )
    assert user_role_property["constrained_columns"] == [
        "property_id",
        "tenant_id",
    ]
    assert user_role_property["referred_columns"] == ["id", "tenant_id"]


def test_gate_a_unique_constraints_are_exact(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected = {
        "roles": {"uq_roles_code"},
        "permissions": {"uq_permissions_code"},
        "role_permissions": {"uq_role_permissions_role_permission"},
        "user_roles": {
            "uq_user_roles_system_grant",
            "uq_user_roles_tenant_grant",
            "uq_user_roles_property_grant",
        },
    }

    for table, names in expected.items():
        actual = {
            item["name"] for item in inspector.get_unique_constraints(table)
        }
        assert actual == names


def test_gate_a_checks_are_enforced_by_mysql(test_engine: Engine) -> None:
    expected = {
        "ck_users_status",
        "ck_roles_code_not_blank",
        "ck_permissions_code_not_blank",
        "ck_user_roles_scope_shape",
        "ck_audit_logs_property_requires_tenant",
    }
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT CONSTRAINT_NAME, ENFORCED
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND CONSTRAINT_TYPE = 'CHECK'
                """
            )
        ).all()

    actual = {name: enforced for name, enforced in rows}
    assert expected <= set(actual)
    assert all(actual[name] == "YES" for name in expected)


def test_gate_a_generated_columns_are_stored(test_engine: Engine) -> None:
    expected = {
        "system_scope_marker": "SYSTEM",
        "tenant_scope_key": "TENANT",
        "property_scope_key": "PROPERTY",
    }
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT COLUMN_NAME, GENERATION_EXPRESSION, EXTRA
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'user_roles'
                  AND COLUMN_NAME IN (
                    'system_scope_marker',
                    'tenant_scope_key',
                    'property_scope_key'
                  )
                """
            )
        ).mappings()
        actual = {row["COLUMN_NAME"]: row for row in rows}

    assert set(actual) == set(expected)
    for column_name, scope_type in expected.items():
        assert scope_type in actual[column_name]["GENERATION_EXPRESSION"].upper()
        assert "STORED GENERATED" in actual[column_name]["EXTRA"].upper()


def test_gate_a_indexes_include_scope_and_audit_paths(
    test_engine: Engine,
) -> None:
    inspector = inspect(test_engine)
    expected = {
        "users": {"ix_users_status_id"},
        "role_permissions": {"ix_role_permissions_permission_role"},
        "user_roles": {
            "ix_user_roles_user_scope",
            "ix_user_roles_role_id",
            "ix_user_roles_tenant_property_role",
            "ix_user_roles_property_tenant",
        },
        "audit_logs": {
            "ix_audit_logs_tenant_created",
            "ix_audit_logs_property_created",
            "ix_audit_logs_property_tenant",
            "ix_audit_logs_actor_created",
            "ix_audit_logs_resource",
            "ix_audit_logs_request_id",
        },
    }

    for table, names in expected.items():
        actual = {
            item["name"]
            for item in inspector.get_indexes(table)
            if item["name"].startswith("ix_")
        }
        assert actual == names


def test_gate_a_table_options_are_frozen(test_engine: Engine) -> None:
    inspector = inspect(test_engine)

    for table in TASK_3_TABLES:
        options = inspector.get_table_options(table)
        assert options["mysql_engine"] == "InnoDB"
        assert options["mysql_collate"] == "utf8mb4_0900_ai_ci"
