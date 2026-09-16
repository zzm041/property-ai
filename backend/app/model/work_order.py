"""WorkOrder core, assignment history, logs, attachments, and SLA models."""

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

_STATUS_CHECK = (
    "BINARY status IN ("
    "'PENDING_ANALYSIS', 'PENDING_ASSIGNMENT', 'ASSIGNED', 'ACCEPTED', "
    "'PROCESSING', 'WAIT_CONFIRM', 'COMPLETED', 'CANCELLED', 'CLOSED')"
)
_PROCESS_REASON_CHECK = (
    "BINARY process_reason IN ("
    "'NONE', 'WAITING_OWNER', 'WAITING_PARTS', 'INTERNAL_UNRESOLVED', "
    "'ESCALATED', 'EXTERNAL_COORDINATION', 'REASSIGN_REQUIRED')"
)
_CATEGORY_CHECK = (
    "BINARY category IN ("
    "'PLUMBING', 'ELECTRICAL', 'HVAC', 'CLEANING', 'SECURITY', 'OTHER')"
)
_PRIORITY_CHECK = "BINARY priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')"
_SEVERITY_CHECK = "BINARY severity IN ('LOW', 'MEDIUM', 'HIGH')"


class FileObject(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "file_objects"
    __table_args__ = (
        CheckConstraint(
            "BINARY scanner_status IN ('pending', 'clean', 'blocked', 'skipped_dev')",
            name="scanner_status",
        ),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(storage_key)) > 0",
            name="storage_key_not_blank",
        ),
        UniqueConstraint("storage_key", name="uq_file_objects_storage_key"),
        UniqueConstraint("id", "tenant_id", name="uq_file_objects_id_tenant"),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_file_objects_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_file_objects_tenant_uploader", "tenant_id", "uploader_user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_file_objects_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uploader_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_file_objects_uploader_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scanner_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="skipped_dev",
        server_default=text("'skipped_dev'"),
    )


class SlaPolicy(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        CheckConstraint(_CATEGORY_CHECK, name="category"),
        CheckConstraint(_PRIORITY_CHECK, name="priority"),
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name="status",
        ),
        CheckConstraint("respond_minutes >= 1", name="respond_minutes_positive"),
        CheckConstraint("complete_minutes >= 1", name="complete_minutes_positive"),
        UniqueConstraint("id", "tenant_id", name="uq_sla_policies_id_tenant"),
        UniqueConstraint(
            "tenant_id",
            "property_scope_key",
            "category",
            "priority",
            name="uq_sla_policies_scope_category_priority",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_sla_policies_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_sla_policies_tenant_id", "tenant_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_sla_policies_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    respond_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    complete_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    property_scope_key: Mapped[int] = mapped_column(
        BigInteger,
        Computed(
            "CASE WHEN property_id IS NULL THEN 0 ELSE property_id END",
            persisted=True,
        ),
        nullable=False,
    )


class WorkOrder(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
        CheckConstraint(_PROCESS_REASON_CHECK, name="process_reason"),
        CheckConstraint(_CATEGORY_CHECK, name="category"),
        CheckConstraint(_PRIORITY_CHECK, name="priority"),
        CheckConstraint(_SEVERITY_CHECK, name="severity"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "BINARY status = 'PROCESSING' OR BINARY process_reason = 'NONE'",
            name="process_reason_processing_only",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_work_orders_id_tenant"),
        UniqueConstraint(
            "id",
            "property_id",
            "tenant_id",
            name="uq_work_orders_id_property_tenant",
        ),
        ForeignKeyConstraint(
            ["house_id", "property_id", "tenant_id"],
            ["houses.id", "houses.property_id", "houses.tenant_id"],
            name="fk_work_orders_house_property_tenant_houses",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sla_policy_id", "tenant_id"],
            ["sla_policies.id", "sla_policies.tenant_id"],
            name="fk_work_orders_sla_policy_tenant_sla_policies",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["incident_id", "property_id", "tenant_id"],
            ["incidents.id", "incidents.property_id", "incidents.tenant_id"],
            name="fk_work_orders_incident_scope_incidents",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_work_orders_property_status_created",
            "property_id",
            "status",
            "created_at",
            "id",
        ),
        Index("ix_work_orders_incident_id", "incident_id"),
        Index(
            "ix_work_orders_incident_property_tenant",
            "incident_id",
            "property_id",
            "tenant_id",
        ),
        Index(
            "ix_work_orders_reporter_created",
            "reporter_user_id",
            "created_at",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_work_orders_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    house_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reporter_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_work_orders_reporter_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING_ANALYSIS",
        server_default=text("'PENDING_ANALYSIS'"),
    )
    process_reason: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="NONE",
        server_default=text("'NONE'"),
    )
    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="OTHER",
        server_default=text("'OTHER'"),
    )
    severity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="LOW",
        server_default=text("'LOW'"),
    )
    priority: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="MEDIUM",
        server_default=text("'MEDIUM'"),
    )
    sla_policy_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    incident_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_assignment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class WorkOrderContent(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_contents"
    __table_args__ = (
        UniqueConstraint("work_order_id", name="uq_work_order_contents_work_order_id"),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_work_order_contents_id_tenant",
        ),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_contents_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_contents_work_order_id", "work_order_id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str | None] = mapped_column(mysql.TEXT(), nullable=True)
    asr_text: Mapped[str | None] = mapped_column(mysql.TEXT(), nullable=True)
    extra_notes: Mapped[str | None] = mapped_column(mysql.TEXT(), nullable=True)


class WorkOrderMedia(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_media"
    __table_args__ = (
        CheckConstraint("BINARY kind IN ('IMAGE', 'AUDIO')", name="kind"),
        CheckConstraint(
            "BINARY taken_phase IN ('REPORT', 'BEFORE', 'AFTER', 'ON_SITE')",
            name="taken_phase",
        ),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_media_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["file_id", "tenant_id"],
            ["file_objects.id", "file_objects.tenant_id"],
            name="fk_work_order_media_file_tenant_file_objects",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_media_work_order_id", "work_order_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    file_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    taken_phase: Mapped[str] = mapped_column(String(16), nullable=False)
    uploader_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_work_order_media_uploader_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )


class WorkOrderAssignment(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_assignments"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED', 'REJECTED', 'REASSIGNED')",
            name="status",
        ),
        UniqueConstraint(
            "work_order_id",
            "active_assignment_key",
            name="uq_work_order_assignments_active_work_order",
        ),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_assignments_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_work_order_assignments_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_work_order_assignments_employee_status",
            "employee_id",
            "status",
            "id",
        ),
        Index(
            "ix_work_order_assignments_work_order_id",
            "work_order_id",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assigned_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_work_order_assignments_assigned_by_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    assigned_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    accept_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_assignment_key: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN BINARY status = 'ACTIVE' AND ended_at IS NULL THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class WorkOrderLog(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "work_order_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_logs_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_work_order_logs_work_order_created",
            "work_order_id",
            "created_at",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_work_order_logs_actor_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    process_reason: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="NONE",
        server_default=text("'NONE'"),
    )
    event_code: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class WorkOrderProcess(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_processes"
    __table_args__ = (
        UniqueConstraint(
            "work_order_id",
            "cycle_no",
            name="uq_work_order_processes_work_order_cycle",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_work_order_processes_id_tenant"),
        UniqueConstraint(
            "id",
            "work_order_id",
            "tenant_id",
            "property_id",
            name="uq_work_order_processes_id_work_order_scope",
        ),
        CheckConstraint("cycle_no >= 1", name="cycle_no_positive"),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_processes_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assignee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_work_order_processes_assignee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_processes_work_order_id", "work_order_id", "cycle_no"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cycle_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assignee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class WorkOrderReceipt(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_receipts"
    __table_args__ = (
        UniqueConstraint("process_id", name="uq_work_order_receipts_process_id"),
        CheckConstraint("resolved IN (0, 1)", name="resolved_boolean"),
        ForeignKeyConstraint(
            ["process_id", "work_order_id", "tenant_id", "property_id"],
            [
                "work_order_processes.id",
                "work_order_processes.work_order_id",
                "work_order_processes.tenant_id",
                "work_order_processes.property_id",
            ],
            name="fk_work_order_receipts_process_scope_work_order_processes",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_receipts_work_order_id", "work_order_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    problem_type: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(255), nullable=False)
    materials: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fee_flag: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="NONE",
        server_default=text("'NONE'"),
    )
    resolved: Mapped[int] = mapped_column(
        mysql.TINYINT(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    unresolved_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    follow_up: Mapped[str | None] = mapped_column(String(255), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WorkOrderFeedback(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_order_feedbacks"
    __table_args__ = (
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="rating"),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_work_order_feedbacks_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["process_id", "work_order_id", "tenant_id", "property_id"],
            [
                "work_order_processes.id",
                "work_order_processes.work_order_id",
                "work_order_processes.tenant_id",
                "work_order_processes.property_id",
            ],
            name="fk_work_order_feedbacks_process_scope_work_order_processes",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_work_order_feedbacks_work_order_id", "work_order_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    process_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_work_order_feedbacks_actor_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    confirmed: Mapped[int | None] = mapped_column(mysql.TINYINT(), nullable=True)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unresolved_flag: Mapped[int] = mapped_column(
        mysql.TINYINT(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class SlaClock(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sla_clocks"
    __table_args__ = (
        CheckConstraint(
            "BINARY state IN ('OPEN', 'WARNING', 'OVERDUE', 'STOPPED')",
            name="state",
        ),
        UniqueConstraint("work_order_id", name="uq_sla_clocks_work_order_id"),
        ForeignKeyConstraint(
            ["work_order_id", "property_id", "tenant_id"],
            ["work_orders.id", "work_orders.property_id", "work_orders.tenant_id"],
            name="fk_sla_clocks_work_order_scope_work_orders",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_sla_clocks_state_complete_due", "state", "complete_due_at"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    work_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accept_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    respond_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    complete_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    warning_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="OPEN",
        server_default=text("'OPEN'"),
    )


class IdempotencyRecord(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_idempotency_records_key"),
        CheckConstraint(
            "CHAR_LENGTH(TRIM(idempotency_key)) > 0",
            name="idempotency_key_not_blank",
        ),
        Index(
            "ix_idempotency_records_user_resource",
            "user_id",
            "resource_type",
            "resource_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_idempotency_records_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    tenant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
