"""Employee identity, assignment history, attendance, and daily status."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
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


class Department(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name="status",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(code)) > 0", name="code_not_blank"),
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        UniqueConstraint("id", "tenant_id", name="uq_departments_id_tenant"),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_departments_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_departments_tenant_id", "tenant_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_departments_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )


class Duty(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "duties"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name="status",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(code)) > 0", name="code_not_blank"),
        UniqueConstraint("tenant_id", "code", name="uq_duties_tenant_code"),
        UniqueConstraint("id", "tenant_id", name="uq_duties_id_tenant"),
        Index("ix_duties_tenant_id", "tenant_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_duties_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )


class Skill(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name="status",
        ),
        CheckConstraint("CHAR_LENGTH(TRIM(code)) > 0", name="code_not_blank"),
        UniqueConstraint("tenant_id", "code", name="uq_skills_tenant_code"),
        UniqueConstraint("id", "tenant_id", name="uq_skills_id_tenant"),
        Index("ix_skills_tenant_id", "tenant_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_skills_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )


class Employee(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'DISABLED')",
            name="status",
        ),
        CheckConstraint("max_capacity >= 1", name="max_capacity_positive"),
        UniqueConstraint("user_id", name="uq_employees_user_id"),
        UniqueConstraint(
            "tenant_id",
            "employee_no",
            name="uq_employees_tenant_employee_no",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_employees_id_tenant"),
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            name="fk_employees_department_tenant_departments",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_employees_tenant_status_id", "tenant_id", "status", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_employees_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_employees_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    employee_no: Mapped[str] = mapped_column(String(32), nullable=False)
    department_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    max_capacity: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


class EmployeeProperty(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_properties"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'ENDED', 'REVOKED')",
            name="status",
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
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_properties_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_employee_properties_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "employee_id",
            "active_property_key",
            name="uq_employee_properties_active_property",
        ),
        Index(
            "ix_employee_properties_employee_property_status_valid_to",
            "employee_id",
            "property_id",
            "status",
            "valid_to",
        ),
        Index(
            "ix_employee_properties_property_status",
            "tenant_id",
            "property_id",
            "status",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
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
    active_property_key: Mapped[int | None] = mapped_column(
        mysql.BIGINT(),
        Computed(
            "CASE WHEN valid_to IS NULL THEN property_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )


class EmployeeDuty(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_duties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_duties_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["duty_id", "tenant_id"],
            ["duties.id", "duties.tenant_id"],
            name="fk_employee_duties_duty_tenant_duties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "employee_id",
            "duty_id",
            name="uq_employee_duties_employee_duty",
        ),
        Index("ix_employee_duties_employee_id", "employee_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duty_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EmployeeSkill(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_skills_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_id", "tenant_id"],
            ["skills.id", "skills.tenant_id"],
            name="fk_employee_skills_skill_tenant_skills",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "employee_id",
            "skill_id",
            name="uq_employee_skills_employee_skill",
        ),
        Index("ix_employee_skills_employee_id", "employee_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    skill_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AttendanceRecord(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        CheckConstraint(
            "BINARY source IN ('STAFF_SELF', 'INTERNAL')",
            name="source",
        ),
        CheckConstraint(
            "check_out_at IS NULL OR check_out_at >= check_in_at",
            name="checkout_after_checkin",
        ),
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_attendance_records_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_attendance_records_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "employee_id",
            "property_id",
            "business_date",
            name="uq_attendance_records_employee_property_date",
        ),
        Index(
            "ix_attendance_records_property_date",
            "tenant_id",
            "property_id",
            "business_date",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    check_out_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="STAFF_SELF",
        server_default=text("'STAFF_SELF'"),
    )


class EmployeeDailyStatus(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_daily_statuses"
    __table_args__ = (
        CheckConstraint(
            "BINARY on_duty IN ('ON_DUTY', 'OFF_DUTY', 'LEAVE')",
            name="on_duty",
        ),
        CheckConstraint("current_load >= 0", name="current_load_nonnegative"),
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_daily_statuses_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_employee_daily_statuses_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "employee_id",
            "property_id",
            "business_date",
            name="uq_employee_daily_statuses_employee_property_date",
        ),
        Index(
            "ix_employee_daily_statuses_property_date_on_duty",
            "property_id",
            "business_date",
            "on_duty",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    on_duty: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="OFF_DUTY",
        server_default=text("'OFF_DUTY'"),
    )
    leave_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_load: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class EmployeeInvite(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_invites"
    __table_args__ = (
        CheckConstraint(
            "BINARY status IN ('ACTIVE', 'CONSUMED', 'REVOKED', 'EXPIRED')",
            name="status",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint("token_hash", name="uq_employee_invites_token_hash"),
        UniqueConstraint(
            "employee_id",
            "active_invite_key",
            name="uq_employee_invites_active_employee",
        ),
        ForeignKeyConstraint(
            ["employee_id", "tenant_id"],
            ["employees.id", "employees.tenant_id"],
            name="fk_employee_invites_employee_tenant_employees",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_phone_e164: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    inviter_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_employee_invites_inviter_user_id_users",
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
    claimed_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "users.id",
            name="fk_employee_invites_claimed_user_id_users",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active_invite_key: Mapped[int | None] = mapped_column(
        mysql.TINYINT(),
        Computed(
            "CASE WHEN BINARY status = 'ACTIVE' THEN 1 ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
