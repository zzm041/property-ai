"""The verification schema must be isolated and must not mutate product metadata."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.mysql import dialect

spec = importlib.util.spec_from_file_location("probe_schema", Path(__file__).parents[1] / "probe_schema.py")
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)


@pytest.mark.parametrize("target", [
    "mysql+pymysql://root:fixture@127.0.0.1:3306/property_ai_test",
    "mysql+pymysql://root:fixture@127.0.0.1:13337/property_ai",
    "mysql+pymysql://root:fixture@example.invalid:13337/property_ai_test",
])
def test_refuses_other_targets_without_any_database_operation(target):
    engine = SimpleNamespace(url=make_url(target))
    with pytest.raises(ValueError, match="owned probe"):
        schema.create_probe_schema(engine)


def test_refuses_existing_schema_even_on_correct_target(monkeypatch):
    engine = SimpleNamespace(url=make_url("mysql+pymysql://root:fixture@127.0.0.1:13337/property_ai_test"))
    monkeypatch.setattr(schema, "inspect", lambda _: SimpleNamespace(get_table_names=lambda: ["users"]), raising=False)
    with pytest.raises(ValueError, match="empty"):
        schema.create_probe_schema(engine)


def test_generated_schema_enables_auto_assignment_without_changing_orm_metadata(monkeypatch):
    from app.model import Base
    original = Base.metadata.tables["work_order_assignments"]
    before = set(original.c)
    engine = SimpleNamespace(url=make_url("mysql+pymysql://root:fixture@127.0.0.1:13337/property_ai_test"))
    monkeypatch.setattr(schema, "inspect", lambda _: SimpleNamespace(get_table_names=lambda: []), raising=False)
    created = []
    monkeypatch.setattr(MetaData, "create_all", lambda metadata, bind: created.append(metadata))
    schema.create_probe_schema(engine)
    generated = created[0].tables["work_order_assignments"]
    assert generated.c.assigned_by_user_id.nullable
    assert {"assigned_source", "payload_json"} <= set(generated.c.keys())
    ddl = str(CreateTable(generated).compile(dialect=dialect()))
    assert "ck_work_order_assignments_source_actor" in ddl
    assert "AUTO" in ddl and "HUMAN" in ddl
    assert set(original.c) == before and not original.c.assigned_by_user_id.nullable
