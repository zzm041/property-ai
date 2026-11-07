"""employee attendance foundation

Revision ID: 20260916_0004
Revises: 20260916_0003
Create Date: 2026-09-16 23:40:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "20260916_0004"
down_revision: str | None = "20260916_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
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
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name=op.f("ck_departments_status"),
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(code)) > 0",
            name=op.f("ck_departments_code_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_departments_property_tenant_properties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_departments_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_departments_id_tenant"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_departments_tenant_id",
        "departments",
        ["tenant_id", "id"],
        unique=False,
    )
    op.create_table(
        "duties",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
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
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name=op.f("ck_duties_status"),
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(code)) > 0",
            name=op.f("ck_duties_code_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_duties_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duties")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_duties_id_tenant"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_duties_tenant_code"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_duties_tenant_id", "duties", ["tenant_id", "id"], unique=False)
    op.create_table(
        "skills",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
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
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name=op.f("ck_skills_status"),
        ),
        sa.CheckConstraint(
            "CHAR_LENGTH(TRIM(code)) > 0",
            name=op.f("ck_skills_code_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_skills_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_skills_id_tenant"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_skills_tenant_code"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_skills_tenant_id", "skills", ["tenant_id", "id"], unique=False)
    op.create_table(
        "employees",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_no", sa.String(length=32), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "max_capacity",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
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
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name=op.f("ck_employees_status"),
        ),
        sa.CheckConstraint(
            "max_capacity >= 1",
            name=op.f("ck_employees_max_capacity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            name="fk_employees_department_tenant_departments",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_employees_tenant_id_tenants",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_employees_user_id_users",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
        sa.UniqueConstraint("id", "tenant_id", name="uq_employees_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_no",
            name="uq_employees_tenant_employee_no",
        ),
        sa.UniqueConstraint("user_id", name="uq_employees_user_id"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_employees_tenant_status_id",
        "employees",
        ["tenant_id", "status", "id"],
        unique=False,
    )
    op.create_table(
        "employee_properties",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column("valid_from", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("valid_to", mysql.DATETIME(fsp=6), nullable=True),
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
        sa.Column(
            "active_property_key",
            mysql.BIGINT(),
            sa.Computed(
                "CASE WHEN valid_to IS NULL THEN property_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED', 'REVOKED')",
            name=op.f("ck_employee_properties_status"),
        ),
        sa.CheckConstraint(
            "("
            "BINARY status = 'ACTIVE' AND valid_to IS NULL"
            ") OR ("
            "BINARY status IN ('ENDED', 'REVOKED') AND valid_to IS NOT NULL"
            ")",
            name=op.f("ck_employee_properties_active_window"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_properties_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_employee_properties_property_tenant_properties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_properties")),
        sa.UniqueConstraint(
            "employee_id",
            "active_property_key",
            name="uq_employee_properties_active_property",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_employee_properties_employee_property_status_valid_to",
        "employee_properties",
        ["employee_id", "property_id", "status", "valid_to"],
        unique=False,
    )
    op.create_index(
        "ix_employee_properties_property_status",
        "employee_properties",
        ["tenant_id", "property_id", "status", "id"],
        unique=False,
    )
    op.create_table(
        "employee_duties",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("duty_id", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["duty_id", "tenant_id"],
            ["duties.id", "duties.tenant_id"],
            name="fk_employee_duties_duty_tenant_duties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_duties_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_duties")),
        sa.UniqueConstraint(
            "employee_id",
            "duty_id",
            name="uq_employee_duties_employee_duty",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_employee_duties_employee_id",
        "employee_duties",
        ["employee_id", "id"],
        unique=False,
    )
    op.create_table(
        "employee_skills",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_id", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_skills_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id", "tenant_id"],
            ["skills.id", "skills.tenant_id"],
            name="fk_employee_skills_skill_tenant_skills",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_skills")),
        sa.UniqueConstraint(
            "employee_id",
            "skill_id",
            name="uq_employee_skills_employee_skill",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_employee_skills_employee_id",
        "employee_skills",
        ["employee_id", "id"],
        unique=False,
    )
    op.create_table(
        "attendance_records",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("check_in_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("check_out_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default=sa.text("'STAFF_SELF'"),
            nullable=False,
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
            "BINARY source IN ('STAFF_SELF', 'INTERNAL')",
            name=op.f("ck_attendance_records_source"),
        ),
        sa.CheckConstraint(
            "check_out_at IS NULL OR check_out_at >= check_in_at",
            name=op.f("ck_attendance_records_checkout_after_checkin"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_attendance_records_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_attendance_records_property_tenant_properties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_records")),
        sa.UniqueConstraint(
            "employee_id",
            "property_id",
            "business_date",
            name="uq_attendance_records_employee_property_date",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_attendance_records_property_date",
        "attendance_records",
        ["tenant_id", "property_id", "business_date", "id"],
        unique=False,
    )
    op.create_table(
        "employee_daily_statuses",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("property_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column(
            "on_duty",
            sa.String(length=32),
            server_default=sa.text("'OFF_DUTY'"),
            nullable=False,
        ),
        sa.Column("leave_type", sa.String(length=32), nullable=True),
        sa.Column(
            "current_load",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
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
            "BINARY on_duty IN ('ON_DUTY', 'OFF_DUTY', 'LEAVE')",
            name=op.f("ck_employee_daily_statuses_on_duty"),
        ),
        sa.CheckConstraint(
            "current_load >= 0",
            name=op.f("ck_employee_daily_statuses_current_load_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_daily_statuses_employee_tenant_employees",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_employee_daily_statuses_property_tenant_properties",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_daily_statuses")),
        sa.UniqueConstraint(
            "employee_id",
            "property_id",
            "business_date",
            name="uq_employee_daily_statuses_employee_property_date",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_employee_daily_statuses_property_date_on_duty",
        "employee_daily_statuses",
        ["property_id", "business_date", "on_duty"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("employee_daily_statuses")
    op.drop_table("attendance_records")
    op.drop_table("employee_skills")
    op.drop_table("employee_duties")
    op.drop_table("employee_properties")
    op.drop_table("employees")
    op.drop_table("skills")
    op.drop_table("duties")
    op.drop_table("departments")
