"""Identity, RBAC, scoped grants, and append-only audit models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.model.base import Base, BigIntPrimaryKeyMixin, TimestampMixin

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _created_at_column() -> MappedColumn[datetime]:
    return mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )


class User(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED', 'MERGED')",
            name="status",
        ),
        Index("ix_users_status_id", "status", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)


class Role(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "CHAR_LENGTH(TRIM(code)) > 0",
            name="code_not_blank",
        ),
        UniqueConstraint("code", name="uq_roles_code"),
        MYSQL_TABLE_OPTIONS,
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class Permission(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"
    __table_args__ = (
        CheckConstraint(
            "CHAR_LENGTH(TRIM(code)) > 0",
            name="code_not_blank",
        ),
        UniqueConstraint("code", name="uq_permissions_code"),
        MYSQL_TABLE_OPTIONS,
    )

    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class RolePermission(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_role_permissions_role_permission",
        ),
        Index(
            "ix_role_permissions_permission_role",
            "permission_id",
            "role_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "roles.id",
            name="fk_role_permissions_role_id_roles",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "permissions.id",
            name="fk_role_permissions_permission_id_permissions",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at_column()


class UserRole(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        CheckConstraint(
            "("
            "BINARY scope_type = 'SYSTEM' "
            "AND tenant_id IS NULL AND property_id IS NULL"
            ") OR ("
            "BINARY scope_type = 'TENANT' "
            "AND tenant_id IS NOT NULL AND property_id IS NULL"
            ") OR ("
            "BINARY scope_type = 'PROPERTY' "
            "AND tenant_id IS NOT NULL AND property_id IS NOT NULL"
            ")",
            name="scope_shape",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_user_roles_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "role_id",
            "system_scope_marker",
            name="uq_user_roles_system_grant",
        ),
        UniqueConstraint(
            "user_id",
            "role_id",
            "tenant_scope_key",
            name="uq_user_roles_tenant_grant",
        ),
        UniqueConstraint(
            "user_id",
            "role_id",
            "property_scope_key",
            name="uq_user_roles_property_grant",
        ),
        Index(
            "ix_user_roles_user_scope",
            "user_id",
            "scope_type",
            "tenant_id",
            "property_id",
            "role_id",
        ),
        Index("ix_user_roles_role_id", "role_id", "id"),
        Index(
            "ix_user_roles_tenant_property_role",
            "tenant_id",
            "property_id",
            "role_id",
            "user_id",
        ),
        Index(
            "ix_user_roles_property_tenant",
            "property_id",
            "tenant_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_user_roles_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "roles.id",
            name="fk_user_roles_role_id_roles",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_user_roles_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    system_scope_marker: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN BINARY scope_type = 'SYSTEM' THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    tenant_scope_key: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed(
            "CASE WHEN BINARY scope_type = 'TENANT' "
            "THEN tenant_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    property_scope_key: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed(
            "CASE WHEN BINARY scope_type = 'PROPERTY' "
            "THEN property_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = _created_at_column()


class UserWechat(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_wechats"
    __table_args__ = (
        UniqueConstraint("appid", "openid", name="uq_user_wechats_appid_openid"),
        UniqueConstraint("unionid", name="uq_user_wechats_unionid"),
        UniqueConstraint("id", "user_id", name="uq_user_wechats_id_user"),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_wechats_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_user_wechats_user_id", "user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    appid: Mapped[str] = mapped_column(String(64), nullable=False)
    openid: Mapped[str] = mapped_column(String(64), nullable=False)
    unionid: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditLog(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "property_id IS NULL OR tenant_id IS NOT NULL",
            name="property_requires_tenant",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_audit_logs_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_audit_logs_tenant_created",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audit_logs_property_created",
            "tenant_id",
            "property_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audit_logs_property_tenant",
            "property_id",
            "tenant_id",
        ),
        Index(
            "ix_audit_logs_actor_created",
            "actor_user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audit_logs_resource",
            "resource_type",
            "resource_id",
            "created_at",
            "id",
        ),
        Index("ix_audit_logs_request_id", "request_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_audit_logs_actor_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_audit_logs_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = _created_at_column()
