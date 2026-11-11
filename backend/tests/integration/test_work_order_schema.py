"""Task 6 schema, BIGINT keys, composite house FK, unique and checks on MySQL 8."""

from sqlalchemy import Engine, inspect, text

from app.model.work_order import WorkOrder, WorkOrderAssignment


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


def test_task_6_tables_exist_without_task_8(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_6_TABLES <= tables
    assert "knowledge_documents" in tables
    assert "knowledge_chunks" in tables


def test_task_6_primary_keys_are_bigint(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    for table in TASK_6_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert columns["id"]["autoincrement"] is True
        assert str(columns["id"]["type"]).lower().startswith("bigint")
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]


def test_work_order_composite_house_foreign_key(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    foreign_key = next(
        item
        for item in inspector.get_foreign_keys("work_orders")
        if item["name"] == "fk_work_orders_house_property_tenant_houses"
    )
    assert foreign_key["constrained_columns"] == [
        "house_id",
        "property_id",
        "tenant_id",
    ]
    assert foreign_key["referred_table"] == "houses"
    assert foreign_key["referred_columns"] == ["id", "property_id", "tenant_id"]


def test_assignment_active_unique_and_employee_scope_fk(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    uniques = {
        item["name"]: item["column_names"]
        for item in inspector.get_unique_constraints("work_order_assignments")
    }
    assert uniques["uq_work_order_assignments_active_work_order"] == [
        "work_order_id",
        "active_assignment_key",
    ]
    foreign_key = next(
        item
        for item in inspector.get_foreign_keys("work_order_assignments")
        if item["name"] == "fk_work_order_assignments_employee_tenant_employees"
    )
    assert foreign_key["constrained_columns"] == ["employee_id", "tenant_id"]
    assert WorkOrderAssignment.__table__.c.active_assignment_key.computed is not None
    assert WorkOrderAssignment.__table__.c.active_assignment_key.computed.persisted is True


def test_idempotency_key_is_unique(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    uniques = {
        item["name"]: item["column_names"]
        for item in inspector.get_unique_constraints("idempotency_records")
    }
    # D1: uniqueness is scoped to the owning user; the replay lookup is
    # (user_id, idempotency_key) and a global key would let one user block another.
    assert uniques["uq_idempotency_records_user_key"] == ["user_id", "idempotency_key"]
    assert "uq_idempotency_records_key" not in uniques


def test_work_order_log_is_append_only_model() -> None:
    columns = set(WorkOrder.__table__.c.keys())
    log_columns = set(
        __import__("app.model.work_order", fromlist=["WorkOrderLog"]).WorkOrderLog.__table__.c.keys()
    )
    assert "updated_at" in columns
    assert "updated_at" not in log_columns
    assert "reporter_user_id" in columns
    assert "creator_user_id" not in columns


def test_status_and_process_reason_checks(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'work_orders'
                  AND CONSTRAINT_TYPE = 'CHECK'
                """
            )
        ).all()
    names = {row[0] for row in rows}
    assert "ck_work_orders_status" in names
    assert "ck_work_orders_process_reason_processing_only" in names
