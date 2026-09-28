"""official auth identity dispatch

Revision ID: 20260919_0009
Revises: 20260917_0007
Create Date: 2026-09-19 08:00:00.000000

Draft only. Do not occupy 0008 (reserved outbox/claim).
Do not apply to property_ai or property_ai_day4 without explicit approval.
If 0008 is created first, retarget this revision to revise 0008.
Otherwise later 0008 must revise 0009. Never create sibling heads.
"""

from collections.abc import Sequence
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260919_0009"
down_revision: str | None = "20260917_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _require_isolated_auth_schema() -> None:
    bind = op.get_bind()
    url = bind.engine.url
    name = bind.execute(sa.text("SELECT DATABASE()" )).scalar()
    delivery_target = f"{url.host}:{url.port}/{url.database}"
    approved_delivery = (
        os.environ.get("PROPERTY_AI_DELIVERY_MIGRATION") == "approved"
        and os.environ.get("PROPERTY_AI_APPROVE_DELIVERY_TARGET") == delivery_target
        and os.environ.get("APP_ENV") == "staging"
        and url.drivername == "mysql+pymysql"
        and url.host == "mysql"
        and url.port == 3306
        and name == "property_ai_delivery_check"
    )
    if name != "property_ai_auth001_test" and not approved_delivery:
        raise RuntimeError(
            "Migration 20260919_0009 requires AUTH001 or an explicit delivery target, "
            f"not {name!r}"
        )


def upgrade() -> None:
    _require_isolated_auth_schema()
    op.add_column(
        "users",
        sa.Column("profile_completed_at", mysql.DATETIME(fsp=6), nullable=True),
    )

    op.create_table(
        "user_phones",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("verified_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("ended_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "active_phone_key",
            mysql.TINYINT(),
            sa.Computed(
                "CASE WHEN BINARY status = 'ACTIVE' THEN 1 ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
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
            "BINARY status IN ('ACTIVE', 'ENDED')",
            name="ck_user_phones_status",
        ),
        sa.CheckConstraint(
            "("
            "BINARY status = 'ACTIVE' AND ended_at IS NULL AND verified_at IS NOT NULL"
            ") OR ("
            "BINARY status = 'ENDED' AND ended_at IS NOT NULL"
            ")",
            name="ck_user_phones_active_window",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_phones_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_phones"),
        sa.UniqueConstraint("phone_e164", "active_phone_key", name="uq_user_phones_active_e164"),
        sa.UniqueConstraint("user_id", "active_phone_key", name="uq_user_phones_active_user"),
        **_TABLE_KW,
    )
    op.create_index("ix_user_phones_user_id", "user_phones", ["user_id", "id"])

    op.create_table(
        "user_passwords",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("password_algo", sa.String(length=32), server_default=sa.text("'argon2id'"), nullable=False),
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
            "BINARY password_algo = 'argon2id'",
            name="ck_user_passwords_algo",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_passwords_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_passwords"),
        sa.UniqueConstraint("user_id", name="uq_user_passwords_user_id"),
        **_TABLE_KW,
    )

    op.create_table(
        "sms_challenges",
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("consumed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("client_fingerprint", sa.String(length=128), nullable=True),
        sa.Column(
            "open_challenge_key",
            mysql.TINYINT(),
            sa.Computed(
                "CASE WHEN consumed_at IS NULL THEN 1 ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "BINARY purpose IN ('login', 'reset', 'change_phone', 'invite')",
            name="ck_sms_challenges_purpose",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_sms_challenges_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_sms_challenges_max_attempts"),
        sa.PrimaryKeyConstraint("id", name="pk_sms_challenges"),
        sa.UniqueConstraint(
            "phone_e164",
            "purpose",
            "open_challenge_key",
            name="uq_sms_challenges_open_phone_purpose",
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_sms_challenges_phone_purpose",
        "sms_challenges",
        ["phone_e164", "purpose", "id"],
    )

    op.create_table(
        "auth_sessions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("replaced_by_id", sa.BigInteger(), nullable=True),
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
            "BINARY auth_method IN ('sms', 'password', 'wechat')",
            name="ck_auth_sessions_auth_method",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_hash"),
        **_TABLE_KW,
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id", "id"])

    op.create_table(
        "visual_challenges",
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.String(length=64), nullable=False),
        sa.Column("answer_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("consumed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "BINARY purpose IN ('login', 'reset')",
            name="ck_visual_challenges_purpose",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visual_challenges"),
        sa.UniqueConstraint("token_hash", name="uq_visual_challenges_token_hash"),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_visual_challenges_phone_purpose",
        "visual_challenges",
        ["phone_e164", "purpose", "id"],
    )

    op.create_table(
        "password_reset_tickets",
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("consumed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_reset_tickets_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tickets"),
        sa.UniqueConstraint(
            "token_hash", name="uq_password_reset_tickets_token_hash"
        ),
        **_TABLE_KW,
    )
    op.create_index(
        "ix_password_reset_tickets_user_id",
        "password_reset_tickets",
        ["user_id", "id"],
    )

    op.create_table(
        "employee_invites",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("target_phone_e164", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("inviter_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("claimed_user_id", sa.BigInteger(), nullable=True),
        sa.Column("consumed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "active_invite_key",
            mysql.TINYINT(),
            sa.Computed(
                "CASE WHEN BINARY status = 'ACTIVE' THEN 1 ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
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
            "BINARY status IN ('ACTIVE', 'CONSUMED', 'REVOKED', 'EXPIRED')",
            name="ck_employee_invites_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_employee_invites_attempts"),
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_invites_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_user_id"],
            ["users.id"],
            name="fk_employee_invites_inviter_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claimed_user_id"],
            ["users.id"],
            name="fk_employee_invites_claimed_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_invites"),
        sa.UniqueConstraint("token_hash", name="uq_employee_invites_token_hash"),
        sa.UniqueConstraint(
            "employee_id",
            "active_invite_key",
            name="uq_employee_invites_active_employee",
        ),
        **_TABLE_KW,
    )

    op.alter_column(
        "employees",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.add_column(
        "work_order_assignments",
        sa.Column(
            "assigned_source",
            sa.String(length=16),
            server_default=sa.text("'HUMAN'"),
            nullable=False,
        ),
    )
    op.add_column(
        "work_order_assignments",
        sa.Column("payload_json", mysql.JSON(), nullable=True),
    )
    op.alter_column(
        "work_order_assignments",
        "assigned_by_user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_work_order_assignments_source_actor",
        "work_order_assignments",
        "("
        "BINARY assigned_source = 'HUMAN' AND assigned_by_user_id IS NOT NULL"
        ") OR ("
        "BINARY assigned_source = 'AUTO' AND assigned_by_user_id IS NULL"
        ")",
    )

    op.add_column(
        "ai_call_logs",
        sa.Column(
            "provider_mode",
            sa.String(length=16),
            server_default=sa.text("'DEVELOPMENT'"),
            nullable=False,
        ),
    )
    op.execute("UPDATE ai_call_logs SET provider_mode = 'DEVELOPMENT' WHERE provider_mode IS NULL")
    op.create_check_constraint(
        "ck_ai_call_logs_provider_mode",
        "ai_call_logs",
        "BINARY provider_mode IN ('DEVELOPMENT', 'PRODUCTION')",
    )


def downgrade() -> None:
    _require_isolated_auth_schema()
    op.drop_constraint("ck_ai_call_logs_provider_mode", "ai_call_logs", type_="check")
    op.drop_column("ai_call_logs", "provider_mode")
    op.drop_constraint(
        "ck_work_order_assignments_source_actor",
        "work_order_assignments",
        type_="check",
    )
    op.alter_column(
        "work_order_assignments",
        "assigned_by_user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_column("work_order_assignments", "payload_json")
    op.drop_column("work_order_assignments", "assigned_source")
    op.alter_column(
        "employees",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_table("employee_invites")
    op.drop_index(
        "ix_password_reset_tickets_user_id", table_name="password_reset_tickets"
    )
    op.drop_table("password_reset_tickets")
    op.drop_index(
        "ix_visual_challenges_phone_purpose", table_name="visual_challenges"
    )
    op.drop_table("visual_challenges")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_sms_challenges_phone_purpose", table_name="sms_challenges")
    op.drop_table("sms_challenges")
    op.drop_table("user_passwords")
    op.drop_index("ix_user_phones_user_id", table_name="user_phones")
    op.drop_table("user_phones")
    op.drop_column("users", "profile_completed_at")
