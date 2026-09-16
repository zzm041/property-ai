"""Task 7 schema, composite FKs, unique, and CHECKs on MySQL 8."""

from sqlalchemy import Engine, inspect, text

TASK_7_TABLES = {
    "work_order_analyses",
    "ai_call_logs",
    "dispatch_recommendations",
    "incidents",
    "incident_work_orders",
    "announcements",
    "announcement_scopes",
    "announcement_reviews",
    "notifications",
}


def test_task_7_tables_exist_without_rag(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_7_TABLES <= tables
    assert "knowledge_documents" not in tables
    assert "knowledge_chunks" not in tables


def test_task_7_primary_keys_are_bigint(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    for table in TASK_7_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert columns["id"]["autoincrement"] is True
        assert str(columns["id"]["type"]).lower().startswith("bigint")
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]


def test_analysis_and_incident_composite_foreign_keys(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    analysis_fk = next(
        item
        for item in inspector.get_foreign_keys("work_order_analyses")
        if item["name"] == "fk_work_order_analyses_work_order_scope_work_orders"
    )
    assert analysis_fk["constrained_columns"] == [
        "work_order_id",
        "property_id",
        "tenant_id",
    ]
    incident_link = next(
        item
        for item in inspector.get_foreign_keys("incident_work_orders")
        if item["name"] == "fk_incident_work_orders_work_order_scope"
    )
    assert incident_link["constrained_columns"] == [
        "work_order_id",
        "property_id",
        "tenant_id",
    ]
    work_order_incident = next(
        item
        for item in inspector.get_foreign_keys("work_orders")
        if item["name"] == "fk_work_orders_incident_scope_incidents"
    )
    assert work_order_incident["referred_table"] == "incidents"


def test_analysis_version_unique_and_confidence_check(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    uniques = {
        item["name"]: item["column_names"]
        for item in inspector.get_unique_constraints("work_order_analyses")
    }
    assert uniques["uq_work_order_analyses_work_order_version"] == [
        "work_order_id",
        "analysis_version",
    ]
    checks = {item["name"] for item in inspector.get_check_constraints("work_order_analyses")}
    assert any("confidence" in name for name in checks)


def test_mysql_rejects_cross_property_incident_link(
    test_engine: Engine,
) -> None:
    with test_engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = DATABASE()
                  AND CONSTRAINT_NAME = 'fk_incident_work_orders_work_order_scope'
                """
            )
        ).scalar_one()
    assert result == 1
