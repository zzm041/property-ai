"""Fresh synthetic schema matching ORM plus existing 0009 AUTO columns."""
from sqlalchemy import CheckConstraint, Column, JSON, MetaData, String, inspect, text

from app.model import Base


def create_probe_schema(engine):
    url = engine.url
    if (url.drivername, url.host, url.port, url.database) != (
            "mysql+pymysql", "127.0.0.1", 13337, "property_ai_test"):
        raise ValueError("Schema creation requires the owned probe target")
    if inspect(engine).get_table_names():
        raise ValueError("Probe schema must be empty")
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    assignments = metadata.tables["work_order_assignments"]
    # Mirror the already-defined 0009 DDL only in this fresh test schema.
    # No ALTER, migration stamp, migration guard override or ORM mutation.
    assignments.append_column(Column("assigned_source", String(16), nullable=False,
                                     server_default=text("'HUMAN'")))
    assignments.append_column(Column("payload_json", JSON(), nullable=True))
    assignments.c.assigned_by_user_id.nullable = True
    assignments.append_constraint(CheckConstraint(
        "(BINARY assigned_source = 'HUMAN' AND assigned_by_user_id IS NOT NULL) OR "
        "(BINARY assigned_source = 'AUTO' AND assigned_by_user_id IS NULL)",
        name="ck_work_order_assignments_source_actor"))
    metadata.create_all(engine)
