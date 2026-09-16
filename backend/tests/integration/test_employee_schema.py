"""Employee schema, uniqueness, generated keys, and composite FK on MySQL 8."""

from sqlalchemy import Engine, inspect, text

from app.model.employee import Employee, EmployeeProperty


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


def test_task_5_tables_exist(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_5_TABLES <= tables
    assert "work_orders" not in tables
    assert "employee_metrics" not in tables


def test_task_5_primary_keys_are_bigint(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    for table in TASK_5_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert columns["id"]["autoincrement"] is True
        assert str(columns["id"]["type"]).lower().startswith("bigint")
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]


def test_task_5_composite_foreign_keys(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected = {
        "employee_properties": (
            "fk_employee_properties_employee_tenant_employees",
            ["employee_id", "tenant_id"],
            "employees",
            ["id", "tenant_id"],
        ),
        "employee_properties_property": (
            "fk_employee_properties_property_tenant_properties",
            ["property_id", "tenant_id"],
            "properties",
            ["id", "tenant_id"],
        ),
        "employees_department": (
            "fk_employees_department_tenant_departments",
            ["department_id", "tenant_id"],
            "departments",
            ["id", "tenant_id"],
        ),
        "attendance_property": (
            "fk_attendance_records_property_tenant_properties",
            ["property_id", "tenant_id"],
            "properties",
            ["id", "tenant_id"],
        ),
        "daily_property": (
            "fk_employee_daily_statuses_property_tenant_properties",
            ["property_id", "tenant_id"],
            "properties",
            ["id", "tenant_id"],
        ),
    }
    by_name = {
        item["name"]: item
        for table in (
            "employee_properties",
            "employees",
            "attendance_records",
            "employee_daily_statuses",
        )
        for item in inspector.get_foreign_keys(table)
    }
    del expected
    assignment_employee = by_name["fk_employee_properties_employee_tenant_employees"]
    assert assignment_employee["constrained_columns"] == ["employee_id", "tenant_id"]
    assert assignment_employee["referred_table"] == "employees"
    assert assignment_employee["referred_columns"] == ["id", "tenant_id"]
    assignment_property = by_name["fk_employee_properties_property_tenant_properties"]
    assert assignment_property["constrained_columns"] == ["property_id", "tenant_id"]
    assert assignment_property["referred_table"] == "properties"
    department_fk = by_name["fk_employees_department_tenant_departments"]
    assert department_fk["constrained_columns"] == ["department_id", "tenant_id"]
    assert department_fk["referred_table"] == "departments"


def test_task_5_generated_active_assignment_key_is_stored(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT GENERATION_EXPRESSION, EXTRA
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'employee_properties'
                  AND COLUMN_NAME = 'active_property_key'
                """
            )
        ).mappings().one()
    assert "VALID_TO" in row["GENERATION_EXPRESSION"].upper()
    assert "PROPERTY_ID" in row["GENERATION_EXPRESSION"].upper()
    assert "STORED GENERATED" in row["EXTRA"].upper()
    column = EmployeeProperty.__table__.c.active_property_key
    assert column.computed is not None
    assert column.computed.persisted is True


def test_task_5_unique_checks_and_indexes(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    employee_uniques = {
        item["name"] for item in inspector.get_unique_constraints("employees")
    }
    assignment_uniques = {
        item["name"] for item in inspector.get_unique_constraints("employee_properties")
    } | {
        item["name"]
        for item in inspector.get_indexes("employee_properties")
        if item["unique"] and item["name"]
    }
    attendance_uniques = {
        item["name"]
        for item in inspector.get_unique_constraints("attendance_records")
    }
    daily_uniques = {
        item["name"]
        for item in inspector.get_unique_constraints("employee_daily_statuses")
    }
    assert "uq_employees_user_id" in employee_uniques
    assert "uq_employees_tenant_employee_no" in employee_uniques
    assert "uq_employees_id_tenant" in employee_uniques
    assert "uq_employee_properties_active_property" in assignment_uniques
    assert "uq_attendance_records_employee_property_date" in attendance_uniques
    assert "uq_employee_daily_statuses_employee_property_date" in daily_uniques

    with test_engine.connect() as connection:
        checks = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT CONSTRAINT_NAME
                    FROM information_schema.TABLE_CONSTRAINTS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND CONSTRAINT_TYPE = 'CHECK'
                      AND TABLE_NAME IN (
                        'employees',
                        'employee_properties',
                        'attendance_records',
                        'employee_daily_statuses',
                        'departments',
                        'duties',
                        'skills'
                      )
                    """
                )
            )
        }
        bigint_columns = {
            (row[0], row[1])
            for row in connection.execute(
                text(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME IN (
                        'employees',
                        'employee_properties',
                        'attendance_records',
                        'employee_daily_statuses'
                      )
                      AND COLUMN_NAME IN (
                        'id', 'user_id', 'tenant_id', 'property_id',
                        'employee_id', 'department_id', 'active_property_key'
                      )
                      AND DATA_TYPE = 'bigint'
                    """
                )
            )
        }
        options = connection.execute(
            text(
                """
                SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN (
                    'employees',
                    'employee_properties',
                    'attendance_records',
                    'employee_daily_statuses'
                  )
                """
            )
        ).all()

    assert "ck_employees_status" in checks
    assert "ck_employee_properties_status" in checks
    assert "ck_employee_properties_active_window" in checks
    assert "ck_employee_daily_statuses_on_duty" in checks
    assert "ck_attendance_records_source" in checks
    assert {
        ("employees", "id"),
        ("employees", "user_id"),
        ("employees", "tenant_id"),
        ("employee_properties", "employee_id"),
        ("employee_properties", "property_id"),
        ("attendance_records", "employee_id"),
        ("employee_daily_statuses", "employee_id"),
    } <= bigint_columns
    assert {(name, engine, collation) for name, engine, collation in options} == {
        ("employees", "InnoDB", "utf8mb4_0900_ai_ci"),
        ("employee_properties", "InnoDB", "utf8mb4_0900_ai_ci"),
        ("attendance_records", "InnoDB", "utf8mb4_0900_ai_ci"),
        ("employee_daily_statuses", "InnoDB", "utf8mb4_0900_ai_ci"),
    }


def test_task_5_models_use_user_id_not_phone() -> None:
    columns = set(Employee.__table__.c.keys())
    assert "user_id" in columns
    assert "phone" not in columns
    assert "username" not in columns
    assert "password" not in columns
    assert "employee_no" in columns
