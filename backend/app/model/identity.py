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
    Integer,
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
    profile_completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )


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


class UserPhone(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_phones"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED')",
            name="status",
        ),
        CheckConstraint(
            "("
            "BINARY status = 'ACTIVE' AND ended_at IS NULL AND verified_at IS NOT NULL"
            ") OR ("
            "BINARY status = 'ENDED' AND ended_at IS NOT NULL"
            ")",
            name="active_window",
        ),
        UniqueConstraint(
            "phone_e164",
            "active_phone_key",
            name="uq_user_phones_active_e164",
        ),
        UniqueConstraint(
            "user_id",
            "active_phone_key",
            name="uq_user_phones_active_user",
        ),
        Index("ix_user_phones_user_id", "user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_user_phones_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active_phone_key: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN BINARY status = 'ACTIVE' THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class UserPassword(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_passwords"
    __table_args__ = (
        CheckConstraint(
            "BINARY password_algo = 'argon2id'",
            name="password_algo",
        ),
        UniqueConstraint("user_id", name="uq_user_passwords_user_id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_user_passwords_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_algo: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="argon2id",
        server_default=text("'argon2id'"),
    )


class SmsChallenge(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "sms_challenges"
    __table_args__ = (
        CheckConstraint(
            "BINARY purpose IN ('login', 'reset', 'change_phone', 'invite')",
            name="purpose",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        UniqueConstraint(
            "phone_e164",
            "purpose",
            "open_challenge_key",
            name="uq_sms_challenges_open_phone_purpose",
        ),
        Index(
            "ix_sms_challenges_phone_purpose",
            "phone_e164",
            "purpose",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default=text("5"),
    )
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    client_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    open_challenge_key: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN consumed_at IS NULL THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = _created_at_column()


class AuthSession(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "BINARY auth_method IN ('sms', 'password', 'wechat')",
            name="auth_method",
        ),
        UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_hash"),
        Index("ix_auth_sessions_user_id", "user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_auth_sessions_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    auth_method: Mapped[str] = mapped_column(String(32), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class VisualChallenge(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "visual_challenges"
    __table_args__ = (
        CheckConstraint(
            "BINARY purpose IN ('login', 'reset')",
            name="purpose",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint("token_hash", name="uq_visual_challenges_token_hash"),
        Index("ix_visual_challenges_phone_purpose", "phone_e164", "purpose", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(String(64), nullable=False)
    answer_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()


class PasswordResetTicket(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "password_reset_tickets"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tickets_token_hash"),
        Index("ix_password_reset_tickets_user_id", "user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_password_reset_tickets_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = _created_at_column()


class IdentityRecoveryCase(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "identity_recovery_cases"
    __table_args__ = (
        UniqueConstraint("purpose", "active_subject_key", name="uq_identity_recovery_active_subject"),
        Index("ix_identity_recovery_status_expiry", "status", "expires_at", "id"),
        Index("ix_identity_recovery_source", "source_user_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    requester_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    source_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True)
    subject_phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    proof_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proof_reference_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proof_result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING_PROOF")
    quarantine_started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    quarantine_ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    proof_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    review_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    impact_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    impact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_subject_key: Mapped[str | None] = mapped_column(String(16), Computed("CASE WHEN BINARY status IN ('PENDING_PROOF','PENDING_REVIEW','APPROVED') THEN subject_phone_e164 ELSE NULL END", persisted=True), nullable=True)


class IdentityRecoveryReview(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "identity_recovery_reviews"
    __table_args__ = (UniqueConstraint("case_id", "reviewer_user_id", name="uq_identity_recovery_distinct_reviewer"), MYSQL_TABLE_OPTIONS)
    case_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("identity_recovery_cases.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at_column()


class PhoneIdentityTombstone(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "phone_identity_tombstones"
    __table_args__ = (
        UniqueConstraint("active_phone_key", name="uq_phone_tombstones_active_phone"),
        Index("ix_phone_tombstones_history", "phone_e164", "id"),
        Index("ix_phone_tombstones_due", "status", "quarantine_until", "id"),
        MYSQL_TABLE_OPTIONS,
    )
    phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    source_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    recovery_case_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("identity_recovery_cases.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUARANTINED")
    quarantine_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active_phone_key: Mapped[str | None] = mapped_column(
        String(16),
        Computed(
            "CASE WHEN BINARY status IN ('QUARANTINED','RELEASED') THEN phone_e164 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class AccountMerge(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_merges"
    __table_args__ = (UniqueConstraint("completed_source_key", name="uq_account_merges_completed_source"), Index("ix_account_merges_target", "target_user_id", "id"), MYSQL_TABLE_OPTIONS)
    source_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    target_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    initiated_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=False)
    source_phone_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user_phones.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True)
    recovery_case_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("identity_recovery_cases.id", ondelete="RESTRICT", onupdate="RESTRICT"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="COMPLETED")
    impact_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    impact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_reauthenticated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_source_key: Mapped[int | None] = mapped_column(BigInteger, Computed("CASE WHEN BINARY status = 'COMPLETED' THEN source_user_id ELSE NULL END", persisted=True), nullable=True)
