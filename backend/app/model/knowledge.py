"""Knowledge documents, versions, chunks, and ingestion jobs. Vectors live in Milvus."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.model.base import Base, BigIntPrimaryKeyMixin, TimestampMixin

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class KnowledgeDocument(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "BINARY visibility IN ('SYSTEM', 'TENANT', 'PROPERTY', 'STAFF_ONLY', 'RESIDENT')",
            name="visibility",
        ),
        CheckConstraint(
            "BINARY status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')",
            name="status",
        ),
        CheckConstraint(
            "BINARY document_type IN ('FAQ', 'GUIDE', 'RULE', 'SOP', 'NOTICE')",
            name="document_type",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_knowledge_documents_id_tenant",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_knowledge_documents_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_knowledge_documents_tenant_property_status",
            "tenant_id",
            "property_id",
            "status",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_knowledge_documents_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_knowledge_documents_created_by_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )


class KnowledgeDocumentVersion(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "BINARY status IN ('PENDING', 'ACTIVE', 'FAILED', 'SUPERSEDED')",
            name="status",
        ),
        UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_document_versions_document_version",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_document_versions_document_tenant",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(mysql.MEDIUMTEXT(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class KnowledgeChunk(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version",
            "chunk_no",
            name="uq_knowledge_chunks_document_version_chunk",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_chunks_document_tenant",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_knowledge_chunks_vector_id", "vector_id"),
        Index(
            "ix_knowledge_chunks_tenant_property_visibility",
            "tenant_id",
            "property_id",
            "visibility",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)
    excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vector_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class KnowledgeIngestionJob(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED')",
            name="status",
        ),
        UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_ingestion_jobs_document_version",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["knowledge_documents.id", "knowledge_documents.tenant_id"],
            name="fk_knowledge_ingestion_jobs_document_tenant",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class NotificationDelivery(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name="status",
        ),
        CheckConstraint("attempt >= 0", name="attempt_nonnegative"),
        ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name="fk_notification_deliveries_notification_id_notifications",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_notification_deliveries_status_next", "status", "next_retry_at", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    notification_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="IN_APP")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
