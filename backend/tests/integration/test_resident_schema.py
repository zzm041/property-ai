"""Resident bind-request schema, uniqueness, and composite FK on MySQL 8."""

from sqlalchemy import Engine, inspect, text

from app.model.property import House
from app.model.resident import ResidentBindRequest, ResidentHouseRelation


def test_task_4_tables_and_house_technical_unique_exist(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    tables = set(inspector.get_table_names())
    assert "resident_bind_requests" in tables
    assert "resident_house_relations" in tables
    house_uniques = {
        item["name"] for item in inspector.get_unique_constraints("houses")
    }
    assert "uq_houses_id_property_tenant" in house_uniques


def test_task_4_composite_house_foreign_keys(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    expected = {
        "resident_bind_requests": "fk_resident_bind_requests_house_scope_houses",
        "resident_house_relations": "fk_resident_house_relations_house_scope_houses",
    }
    for table, name in expected.items():
        foreign_key = next(
            item
            for item in inspector.get_foreign_keys(table)
            if item["name"] == name
        )
        assert foreign_key["constrained_columns"] == [
            "house_id",
            "property_id",
            "tenant_id",
        ]
        assert foreign_key["referred_table"] == "houses"
        assert foreign_key["referred_columns"] == [
            "id",
            "property_id",
            "tenant_id",
        ]


def test_task_4_generated_columns_are_stored(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME, GENERATION_EXPRESSION, EXTRA
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND (
                    (TABLE_NAME = 'resident_bind_requests'
                     AND COLUMN_NAME = 'pending_house_key')
                    OR
                    (TABLE_NAME = 'resident_house_relations'
                     AND COLUMN_NAME = 'active_house_key')
                  )
                """
            )
        ).mappings()
        actual = {
            (row["TABLE_NAME"], row["COLUMN_NAME"]): row for row in rows
        }

    pending = actual[("resident_bind_requests", "pending_house_key")]
    active = actual[("resident_house_relations", "active_house_key")]
    assert "PENDING" in pending["GENERATION_EXPRESSION"].upper()
    assert "HOUSE_ID" in pending["GENERATION_EXPRESSION"].upper()
    assert "STORED GENERATED" in pending["EXTRA"].upper()
    assert "VALID_TO" in active["GENERATION_EXPRESSION"].upper()
    assert "STORED GENERATED" in active["EXTRA"].upper()


def test_task_4_models_use_generated_unique_keys() -> None:
    pending = ResidentBindRequest.__table__.c.pending_house_key
    active = ResidentHouseRelation.__table__.c.active_house_key
    assert pending.computed is not None
    assert pending.computed.persisted is True
    assert active.computed is not None
    assert active.computed.persisted is True
    house_uniques = {
        constraint.name
        for constraint in House.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_houses_id_property_tenant" in house_uniques


def test_task_4_unique_checks_and_indexes(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    request_uniques = {
        item["name"] for item in inspector.get_unique_constraints(
            "resident_bind_requests"
        )
    } | {
        item["name"]
        for item in inspector.get_indexes("resident_bind_requests")
        if item["unique"] and item["name"]
    }
    relation_uniques = {
        item["name"] for item in inspector.get_unique_constraints(
            "resident_house_relations"
        )
    } | {
        item["name"]
        for item in inspector.get_indexes("resident_house_relations")
        if item["unique"] and item["name"]
    }
    assert "uq_resident_bind_requests_pending_house" in request_uniques
    assert "uq_resident_house_relations_active_house" in relation_uniques

    request_indexes = {
        item["name"] for item in inspector.get_indexes("resident_bind_requests")
        if item["name"] and item["name"].startswith("ix_")
    }
    relation_indexes = {
        item["name"] for item in inspector.get_indexes("resident_house_relations")
        if item["name"] and item["name"].startswith("ix_")
    }
    assert {
        "ix_resident_bind_requests_property_status",
        "ix_resident_bind_requests_user_id",
        "ix_resident_bind_requests_house_scope",
    } <= request_indexes
    assert {
        "ix_resident_house_relations_house_status_valid_to",
        "ix_resident_house_relations_user_id",
        "ix_resident_house_relations_property_user",
        "ix_resident_house_relations_house_scope",
    } <= relation_indexes

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
                        'resident_bind_requests',
                        'resident_house_relations'
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
                        'resident_bind_requests',
                        'resident_house_relations'
                      )
                      AND COLUMN_NAME IN (
                        'id', 'user_id', 'tenant_id', 'property_id', 'house_id',
                        'reviewer_id', 'bind_request_id', 'pending_house_key',
                        'active_house_key'
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
                    'resident_bind_requests',
                    'resident_house_relations'
                  )
                """
            )
        ).all()

    assert {
        "ck_resident_bind_requests_status",
        "ck_resident_bind_requests_relation_type",
        "ck_resident_house_relations_status",
        "ck_resident_house_relations_relation_type",
        "ck_resident_house_relations_active_window",
    } <= checks
    assert {
        ("resident_bind_requests", "id"),
        ("resident_bind_requests", "user_id"),
        ("resident_bind_requests", "tenant_id"),
        ("resident_bind_requests", "property_id"),
        ("resident_bind_requests", "house_id"),
        ("resident_bind_requests", "reviewer_id"),
        ("resident_house_relations", "id"),
        ("resident_house_relations", "user_id"),
        ("resident_house_relations", "tenant_id"),
        ("resident_house_relations", "property_id"),
        ("resident_house_relations", "house_id"),
        ("resident_house_relations", "bind_request_id"),
    } <= bigint_columns
    assert {(name, engine, collation) for name, engine, collation in options} == {
        ("resident_bind_requests", "InnoDB", "utf8mb4_0900_ai_ci"),
        ("resident_house_relations", "InnoDB", "utf8mb4_0900_ai_ci"),
    }


def test_task_4_named_foreign_keys(test_engine: Engine) -> None:
    inspector = inspect(test_engine)
    request_fks = {
        item["name"] for item in inspector.get_foreign_keys("resident_bind_requests")
    }
    relation_fks = {
        item["name"] for item in inspector.get_foreign_keys("resident_house_relations")
    }
    assert request_fks == {
        "fk_resident_bind_requests_house_scope_houses",
        "fk_resident_bind_requests_reviewer_id_users",
        "fk_resident_bind_requests_tenant_id_tenants",
        "fk_resident_bind_requests_user_id_users",
    }
    assert relation_fks == {
        "fk_resident_house_relations_bind_request_id_requests",
        "fk_resident_house_relations_house_scope_houses",
        "fk_resident_house_relations_tenant_id_tenants",
        "fk_resident_house_relations_user_id_users",
    }
