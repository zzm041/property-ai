"""Durable typed delivery intents; business records remain authoritative."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.model.base import Base, BigIntPrimaryKeyMixin, TimestampMixin


class TaskIntent(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_intents"
    __table_args__ = (
        CheckConstraint("BINARY operation IN ('ANALYZE_WORK_ORDER','INGEST_KNOWLEDGE','DELIVER_NOTIFICATION')", name="operation"),
        CheckConstraint("BINARY status IN ('PENDING','PUBLISHING','PUBLISHED','PROCESSING','RETRY_WAIT','SUCCEEDED','FAILED','CANCELLED')", name="status"),
        CheckConstraint("resource_id > 0 AND resource_version > 0 AND generation > 0", name="positive_resource"),
        CheckConstraint("publish_attempt >= 0 AND execution_attempt >= 0", name="attempt_nonnegative"),
        CheckConstraint("((BINARY status IN ('PUBLISHING','PROCESSING')) AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR ((BINARY status NOT IN ('PUBLISHING','PROCESSING')) AND lease_token IS NULL AND lease_until IS NULL)", name="lease_consistent"),
        UniqueConstraint("operation", "resource_id", "resource_version", "generation", name="uq_task_intents_resource_generation"),
        UniqueConstraint("task_id", name="uq_task_intents_task_id"),
        ForeignKeyConstraint(["property_id", "tenant_id"], ["properties.id", "properties.tenant_id"], ondelete="RESTRICT", onupdate="RESTRICT", name="fk_task_intents_property_tenant"),
        Index("ix_task_intents_due", "status", "available_at", "id"),
        Index("ix_task_intents_expired", "status", "lease_until", "id"),
        Index("ix_task_intents_scope", "tenant_id", "property_id", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )
    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    property_id: Mapped[int | None] = mapped_column(BigInteger)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii", collation="ascii_bin"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", server_default="PENDING")
    publish_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    execution_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    lease_token: Mapped[str | None] = mapped_column(CHAR(32, charset="ascii", collation="ascii_bin"))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
