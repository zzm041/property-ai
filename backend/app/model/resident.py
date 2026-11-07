"""Resident bind requests and historical house relations."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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


class ResidentBindRequest(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resident_bind_requests"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="status",
        ),
        CheckConstraint(
            "BINARY relation_type IN "
            "('OWNER', 'TENANT', 'FAMILY', 'AUTHORIZED_OTHER')",
            name="relation_type",
        ),
        ForeignKeyConstraint(
            ["house_id", "property_id", "tenant_id"],
            ["houses.id", "houses.property_id", "houses.tenant_id"],
            name="fk_resident_bind_requests_house_scope_houses",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "pending_house_key",
            name="uq_resident_bind_requests_pending_house",
        ),
        Index(
            "ix_resident_bind_requests_property_status",
            "tenant_id",
            "property_id",
            "status",
            "id",
        ),
        Index(
            "ix_resident_bind_requests_user_id",
            "user_id",
            "id",
        ),
        Index(
            "ix_resident_bind_requests_house_scope",
            "house_id",
            "property_id",
            "tenant_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_resident_bind_requests_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_resident_bind_requests_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    house_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
        server_default=text("'PENDING'"),
    )
    auto_check_result: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    reviewer_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_resident_bind_requests_reviewer_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    review_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    pending_house_key: Mapped[int | None] = mapped_column(
        mysql.BIGINT(),
        Computed(
            "CASE WHEN BINARY status = 'PENDING' THEN house_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class ResidentHouseRelation(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "resident_house_relations"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED', 'REVOKED')",
            name="status",
        ),
        CheckConstraint(
            "BINARY relation_type IN "
            "('OWNER', 'TENANT', 'FAMILY', 'AUTHORIZED_OTHER')",
            name="relation_type",
        ),
        CheckConstraint(
            "("
            "BINARY status = 'ACTIVE' AND valid_to IS NULL"
            ") OR ("
            "BINARY status IN ('ENDED', 'REVOKED') AND valid_to IS NOT NULL"
            ")",
            name="active_window",
        ),
        ForeignKeyConstraint(
            ["house_id", "property_id", "tenant_id"],
            ["houses.id", "houses.property_id", "houses.tenant_id"],
            name="fk_resident_house_relations_house_scope_houses",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "active_house_key",
            name="uq_resident_house_relations_active_house",
        ),
        Index(
            "ix_resident_house_relations_house_status_valid_to",
            "house_id",
            "status",
            "valid_to",
        ),
        Index(
            "ix_resident_house_relations_user_id",
            "user_id",
            "id",
        ),
        Index(
            "ix_resident_house_relations_property_user",
            "tenant_id",
            "property_id",
            "user_id",
            "id",
        ),
        Index(
            "ix_resident_house_relations_house_scope",
            "house_id",
            "property_id",
            "tenant_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_resident_house_relations_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_resident_house_relations_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    house_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    bind_request_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "resident_bind_requests.id",
            name="fk_resident_house_relations_bind_request_id_requests",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    active_house_key: Mapped[int | None] = mapped_column(
        mysql.BIGINT(),
        Computed(
            "CASE WHEN valid_to IS NULL THEN house_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
