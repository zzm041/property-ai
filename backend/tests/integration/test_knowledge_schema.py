"""Task 8 schema, unique WeChat identity, and knowledge CHECKs."""

from sqlalchemy import Engine, inspect, text


TASK_8_TABLES = {
    "user_wechats",
    "knowledge_documents",
    "knowledge_document_versions",
    "knowledge_chunks",
    "knowledge_ingestion_jobs",
    "notification_deliveries",
}


def test_task_8_tables_exist(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert TASK_8_TABLES <= tables


def test_task_8_primary_keys_are_bigint(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    for table in TASK_8_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert columns["id"]["autoincrement"] is True
        assert str(columns["id"]["type"]).lower().startswith("bigint")
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]


def test_user_wechat_unique_appid_openid(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    uniques = {item["name"] for item in inspector.get_unique_constraints("user_wechats")}
    assert "uq_user_wechats_appid_openid" in uniques
    assert "uq_user_wechats_unionid" in uniques


def test_knowledge_document_visibility_check(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM information_schema.CHECK_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = DATABASE()
                  AND CONSTRAINT_NAME = 'ck_knowledge_documents_visibility'
                """
            )
        ).all()
    assert rows


def test_ingestion_job_unique_document_version(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    uniques = {
        item["name"]
        for item in inspector.get_unique_constraints("knowledge_ingestion_jobs")
    }
    assert "uq_knowledge_ingestion_jobs_document_version" in uniques
