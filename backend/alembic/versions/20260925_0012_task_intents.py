"""Approved additive AUTH001 durable intents, parent0011; never0010."""
import os
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260925_0012_task_intents"
down_revision = "20260924_0011_contact_phone"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    url = bind.engine.url
    target = f"{url.host}:{url.port}/{url.database}"
    auth001 = (os.environ.get("APP_ENV") == "test" and url.drivername == "mysql+pymysql"
               and url.host == "127.0.0.1" and url.port == 3307
               and url.database == "property_ai_auth001_test")
    delivery = (os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") == "approved"
                and os.environ.get("PROPERTY_AI_APPROVE_DELIVERY_TARGET") == target
                and os.environ.get("APP_ENV") == "staging" and url.drivername == "mysql+pymysql"
                and url.host == "mysql" and url.port == 3306
                and url.database == "property_ai_delivery_check")
    if not auth001 and not delivery:
        raise RuntimeError("0012 approved only for exact AUTH001 test target")
    if bind.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() != down_revision:
        raise RuntimeError("0012 requires actual0011; no stamp/backfill permitted")
    op.create_table(
        "task_intents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("task_id", mysql.CHAR(36, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("publish_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("lease_token", mysql.CHAR(32, charset="ascii", collation="ascii_bin")),
        sa.Column("lease_until", mysql.DATETIME(fsp=6)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("published_at", mysql.DATETIME(fsp=6)),
        sa.Column("finished_at", mysql.DATETIME(fsp=6)),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_task_intents_tenant_id_tenants", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.ForeignKeyConstraint(["property_id", "tenant_id"], ["properties.id", "properties.tenant_id"], name="fk_task_intents_property_tenant", ondelete="RESTRICT", onupdate="RESTRICT"),
        sa.UniqueConstraint("operation", "resource_id", "resource_version", "generation", name="uq_task_intents_resource_generation"),
        sa.UniqueConstraint("task_id", name="uq_task_intents_task_id"),
        sa.CheckConstraint("BINARY operation IN ('ANALYZE_WORK_ORDER','INGEST_KNOWLEDGE','DELIVER_NOTIFICATION')", name="operation"),
        sa.CheckConstraint("BINARY status IN ('PENDING','PUBLISHING','PUBLISHED','PROCESSING','RETRY_WAIT','SUCCEEDED','FAILED','CANCELLED')", name="status"),
        sa.CheckConstraint("resource_id > 0 AND resource_version > 0 AND generation > 0", name="positive_resource"),
        sa.CheckConstraint("publish_attempt >= 0 AND execution_attempt >= 0", name="attempt_nonnegative"),
        sa.CheckConstraint("((BINARY status IN ('PUBLISHING','PROCESSING')) AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR ((BINARY status NOT IN ('PUBLISHING','PROCESSING')) AND lease_token IS NULL AND lease_until IS NULL)", name="lease_consistent"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_task_intents_due", "task_intents", ["status", "available_at", "id"])
    op.create_index("ix_task_intents_expired", "task_intents", ["status", "lease_until", "id"])
    op.create_index("ix_task_intents_scope", "task_intents", ["tenant_id", "property_id", "id"])


def downgrade():
    raise RuntimeError("Dropping durable task history is not approved")
