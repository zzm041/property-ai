"""AI analysis, call logs, dispatch recommendations, incidents, and announcements."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
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


class WorkOrderAnalysis(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_analyses"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ("
            "'PROCESSING', 'COMPLETED', 'FAILED', 'STALE', 'LOW_CONFIDENCE')",
            name="status",
        ),
        CheckConstraint("analysis_version >= 1", name="analysis_version_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        UniqueConstraint(
            "work_order_id",
            "analysis_version",
            name="uq_work_order_analyses_work_order_version",
        ),
        UniqueConstraint(
            "work_order_id",
            "processing_key",
            name="uq_work_order_analyses_processing",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_work_order_analyses_id_tenant"),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_analyses_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_analyses_work_order_id", "work_order_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    required_skill: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_key: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN BINARY status = 'PROCESSING' THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class AiCallLog(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "ai_call_logs"
    __table_args__ = (
        CheckConstraint(
            "BINARY provider_type IN ('LLM', 'VLM', 'ASR')",
            name="provider_type",
        ),
        CheckConstraint(
            "BINARY status IN ('SUCCESS', 'FAILED', 'TIMEOUT', 'INVALID', 'STALE')",
            name="status",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        Index("ix_ai_call_logs_work_order_id", "work_order_id", "id"),
        Index("ix_ai_call_logs_request_id", "request_id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    biz_type: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class DispatchRecommendation(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dispatch_recommendations"
    __table_args__ = (
        CheckConstraint("eligible IN (0, 1)", name="eligible_boolean"),
        UniqueConstraint(
            "analysis_id",
            "employee_id",
            name="uq_dispatch_recommendations_analysis_employee",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "tenant_id"],
            ["work_order_analyses.id", "work_order_analyses.tenant_id"],
            name="fk_dispatch_recommendations_analysis_tenant",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_dispatch_recommendations_work_order_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_dispatch_recommendations_employee_tenant",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_dispatch_recommendations_work_order_id", "work_order_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    analysis_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    rank_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    eligible: Mapped[int] = mapped_column(mysql.TINYINT(), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    filter_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected: Mapped[int] = mapped_column(
        mysql.TINYINT(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class Incident(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('OPEN', 'MONITORING', 'RESOLVED', 'CLOSED')",
            name="status",
        ),
        UniqueConstraint(
            "id",
            "property_id",
            "tenant_id",
            name="uq_incidents_id_property_tenant",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_incidents_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["building_id", "property_id", "tenant_id"],
            ["buildings.id", "buildings.property_id", "buildings.tenant_id"],
            name="fk_incidents_building_scope_buildings",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_incidents_property_status",
            "property_id",
            "status",
            "id",
        ),
        Index(
            "ix_incidents_group",
            "tenant_id",
            "property_id",
            "building_id",
            "category",
            "status",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_incidents_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    building_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    affected_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_work_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    primary_employee_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    work_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class IncidentWorkOrder(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "incident_work_orders"
    __table_args__ = (
        UniqueConstraint("work_order_id", name="uq_incident_work_orders_work_order_id"),
        UniqueConstraint(
            "incident_id",
            "work_order_id",
            name="uq_incident_work_orders_incident_work_order",
        ),
        ForeignKeyConstraint(
            ["incident_id", "property_id", "tenant_id"],
            ["incidents.id", "incidents.property_id", "incidents.tenant_id"],
            name="fk_incident_work_orders_incident_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_incident_work_orders_work_order_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_incident_work_orders_incident_id", "incident_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Announcement(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ("
            "'DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'ARCHIVED', 'REJECTED')",
            name="status",
        ),
        UniqueConstraint(
            "id",
            "property_id",
            "tenant_id",
            name="uq_announcements_id_property_tenant",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_announcements_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["incident_id", "property_id", "tenant_id"],
            ["incidents.id", "incidents.property_id", "incidents.tenant_id"],
            name="fk_announcements_incident_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_announcements_property_status", "property_id", "status", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_announcements_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incident_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(mysql.TEXT(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    ai_draft: Mapped[int] = mapped_column(
        mysql.TINYINT(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_announcements_created_by_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AnnouncementScope(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "announcement_scopes"
    __table_args__ = (
        CheckConstraint(
            "BINARY scope_type IN ('TENANT', 'PROPERTY', 'BUILDING', 'UNIT', 'AUDIENCE')",
            name="scope_type",
        ),
        ForeignKeyConstraint(
            ["announcement_id", "property_id", "tenant_id"],
            ["announcements.id", "announcements.property_id", "announcements.tenant_id"],
            name="fk_announcement_scopes_announcement_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_announcement_scopes_announcement_id", "announcement_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    announcement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    building_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audience: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AnnouncementReview(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "announcement_reviews"
    __table_args__ = (
        CheckConstraint(
            "BINARY decision IN ('APPROVE', 'REJECT')",
            name="decision",
        ),
        ForeignKeyConstraint(
            ["announcement_id", "property_id", "tenant_id"],
            ["announcements.id", "announcements.property_id", "announcements.tenant_id"],
            name="fk_announcement_reviews_announcement_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_announcement_reviews_announcement_id", "announcement_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    announcement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_announcement_reviews_reviewer_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class Notification(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_user_created",
            "user_id",
            "created_at",
            "id",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_notifications_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_notifications_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
