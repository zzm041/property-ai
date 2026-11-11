"""knowledge_wechat_delivery

Revision ID: 20260917_0007
Revises: 20260917_0006
Create Date: 2026-09-17 02:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260917_0007"
down_revision: str | None = "20260917_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_wechats",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("appid", sa.String(length=64), nullable=False),
        sa.Column("openid", sa.String(length=64), nullable=False),
        sa.Column("unionid", sa.String(length=64), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_wechats_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_wechats")),
        sa.UniqueConstraint("appid", "openid", name="uq_user_wechats_appid_openid"),
        sa.UniqueConstraint("unionid", name="uq_user_wechats_unionid"),
        sa.UniqueConstraint("id", "user_id", name="uq_user_wechats_id_user"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_user_wechats_user_id",
        "user_wechats",
        ["user_id", "id"],
        unique=False,
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "BINARY visibility IN ('SYSTEM', 'TENANT', 'PROPERTY', 'STAFF_ONLY', 'RESIDENT')",
            name=op.f("ck_knowledge_documents_visibility"),
        ),
        sa.CheckConstraint(
            "BINARY status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')",
            name=op.f("ck_knowledge_documents_status"),
        ),
        sa.CheckConstraint(
            "BINARY document_type IN ('FAQ', 'GUIDE', 'RULE', 'SOP', 'NOTICE')",
            name=op.f("ck_knowledge_documents_document_type"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_knowledge_documents_created_by_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_knowledge_documents_property_tenant_properties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_knowledge_documents_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_knowledge_documents_id_tenant"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_knowledge_documents_tenant_property_status",
        "knowledge_documents",
        ["tenant_id", "property_id", "status", "id"],
        unique=False,
    )
    op.create_table(
        "knowledge_document_versions",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_text", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_knowledge_document_versions_version_positive")),
        sa.CheckConstraint(
            "BINARY status IN ('PENDING', 'ACTIVE', 'FAILED', 'SUPERSEDED')",
            name=op.f("ck_knowledge_document_versions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_document_versions_document_tenant",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_document_versions")),
        sa.UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_document_versions_document_version",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("source_locator", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("vector_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_chunks_document_tenant",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "version",
            "chunk_no",
            name="uq_knowledge_chunks_document_version_chunk",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_knowledge_chunks_vector_id",
        "knowledge_chunks",
        ["vector_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_property_visibility",
        "knowledge_chunks",
        ["tenant_id", "property_id", "visibility", "id"],
        unique=False,
    )
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "BINARY status IN ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_knowledge_ingestion_jobs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_ingestion_jobs_document_tenant",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_ingestion_jobs")),
        sa.UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_ingestion_jobs_document_version",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("next_retry_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("delivered_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "BINARY status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_notification_deliveries_status"),
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name=op.f("ck_notification_deliveries_attempt_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name="fk_notification_deliveries_notification_id_notifications",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_notification_deliveries_status_next",
        "notification_deliveries",
        ["status", "next_retry_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop tables only. MySQL may reuse Task 8 indexes for foreign keys, so
    # dropping those indexes first fails with errno 1553. Skip already-removed
    # tables so a failed mid-downgrade can be completed.
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in (
        "notification_deliveries",
        "knowledge_ingestion_jobs",
        "knowledge_chunks",
        "knowledge_document_versions",
        "knowledge_documents",
        "user_wechats",
    ):
        if table in existing:
            op.drop_table(table)
