"""resident house binding

Revision ID: 20260916_0003
Revises: 20260915_0002
Create Date: 2026-09-16 23:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260916_0003"
down_revision: str | None = "20260915_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_houses_id_property_tenant",
        "houses",
        ["id", "property_id", "tenant_id"],
    )
    op.create_table(
        "resident_bind_requests",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("house_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("auto_check_result", sa.String(length=64), nullable=True),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=True),
        sa.Column("review_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "requested_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "pending_house_key",
            mysql.BIGINT(),
            sa.Computed(
                "CASE WHEN BINARY status = 'PENDING' THEN house_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
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
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.CheckConstraint(
            "BINARY status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name=op.f("ck_resident_bind_requests_status"),
        ),
        sa.CheckConstraint(
            "BINARY relation_type IN "
            "('OWNER', 'TENANT', 'FAMILY', 'AUTHORIZED_OTHER')",
            name=op.f("ck_resident_bind_requests_relation_type"),
        ),
        sa.ForeignKeyConstraint(
            ["house_id", "property_id", "tenant_id"],
            ["houses.id", "houses.property_id", "houses.tenant_id"],
            name="fk_resident_bind_requests_house_scope_houses",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name="fk_resident_bind_requests_reviewer_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_resident_bind_requests_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_resident_bind_requests_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resident_bind_requests")),
        sa.UniqueConstraint(
            "user_id",
            "pending_house_key",
            name="uq_resident_bind_requests_pending_house",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_resident_bind_requests_house_scope",
        "resident_bind_requests",
        ["house_id", "property_id", "tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_resident_bind_requests_property_status",
        "resident_bind_requests",
        ["tenant_id", "property_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_resident_bind_requests_user_id",
        "resident_bind_requests",
        ["user_id", "id"],
        unique=False,
    )
    op.create_table(
        "resident_house_relations",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("house_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column("valid_from", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("valid_to", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("bind_request_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "active_house_key",
            mysql.BIGINT(),
            sa.Computed(
                "CASE WHEN valid_to IS NULL THEN house_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED', 'REVOKED')",
            name=op.f("ck_resident_house_relations_status"),
        ),
        sa.CheckConstraint(
            "BINARY relation_type IN "
            "('OWNER', 'TENANT', 'FAMILY', 'AUTHORIZED_OTHER')",
            name=op.f("ck_resident_house_relations_relation_type"),
        ),
        sa.CheckConstraint(
            "("
            "BINARY status = 'ACTIVE' AND valid_to IS NULL"
            ") OR ("
            "BINARY status IN ('ENDED', 'REVOKED') AND valid_to IS NOT NULL"
            ")",
            name=op.f("ck_resident_house_relations_active_window"),
        ),
        sa.ForeignKeyConstraint(
            ["bind_request_id"],
            ["resident_bind_requests.id"],
            name="fk_resident_house_relations_bind_request_id_requests",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["house_id", "property_id", "tenant_id"],
            ["houses.id", "houses.property_id", "houses.tenant_id"],
            name="fk_resident_house_relations_house_scope_houses",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_resident_house_relations_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_resident_house_relations_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resident_house_relations")),
        sa.UniqueConstraint(
            "user_id",
            "active_house_key",
            name="uq_resident_house_relations_active_house",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_resident_house_relations_house_scope",
        "resident_house_relations",
        ["house_id", "property_id", "tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_resident_house_relations_house_status_valid_to",
        "resident_house_relations",
        ["house_id", "status", "valid_to"],
        unique=False,
    )
    op.create_index(
        "ix_resident_house_relations_property_user",
        "resident_house_relations",
        ["tenant_id", "property_id", "user_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_resident_house_relations_user_id",
        "resident_house_relations",
        ["user_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("resident_house_relations")
    op.drop_table("resident_bind_requests")
    op.drop_constraint(
        "uq_houses_id_property_tenant",
        "houses",
        type_="unique",
    )
