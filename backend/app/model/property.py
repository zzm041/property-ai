"""Tenant and physical Property hierarchy models."""

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.model.base import Base, BigIntPrimaryKeyMixin, TimestampMixin

MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class Tenant(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("name", name="uq_tenants_name"),
        Index("ix_tenants_status_id", "status", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Property(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_properties_tenant_name",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_properties_id_tenant",
        ),
        Index("ix_properties_tenant_id", "tenant_id", "id"),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tenants.id",
            name="fk_properties_tenant_id_tenants",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Shanghai",
        server_default=text("'Asia/Shanghai'"),
    )


class Building(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "buildings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["property_id", "tenant_id"],
            ["properties.id", "properties.tenant_id"],
            name="fk_buildings_property_tenant_properties",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            name="uq_buildings_tenant_property_code",
        ),
        UniqueConstraint(
            "id",
            "property_id",
            "tenant_id",
            name="uq_buildings_id_property_tenant",
        ),
        Index(
            "ix_buildings_property_tenant",
            "property_id",
            "tenant_id",
        ),
        Index(
            "ix_buildings_tenant_property_id",
            "tenant_id",
            "property_id",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Unit(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "units"
    __table_args__ = (
        ForeignKeyConstraint(
            ["building_id", "property_id", "tenant_id"],
            ["buildings.id", "buildings.property_id", "buildings.tenant_id"],
            name="fk_units_building_property_tenant_buildings",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "building_id",
            "code",
            name="uq_units_tenant_property_building_code",
        ),
        UniqueConstraint(
            "id",
            "property_id",
            "tenant_id",
            name="uq_units_id_property_tenant",
        ),
        Index(
            "ix_units_building_property_tenant",
            "building_id",
            "property_id",
            "tenant_id",
        ),
        Index(
            "ix_units_tenant_property_building_id",
            "tenant_id",
            "property_id",
            "building_id",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    building_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class House(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "houses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["unit_id", "property_id", "tenant_id"],
            ["units.id", "units.property_id", "units.tenant_id"],
            name="fk_houses_unit_property_tenant_units",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "property_id",
            "unit_id",
            "room_no",
            name="uq_houses_tenant_property_unit_room",
        ),
        Index(
            "ix_houses_unit_property_tenant",
            "unit_id",
            "property_id",
            "tenant_id",
        ),
        Index(
            "ix_houses_scope_unit_id",
            "tenant_id",
            "property_id",
            "unit_id",
            "id",
        ),
        Index(
            "ix_houses_tenant_property_status_id",
            "tenant_id",
            "property_id",
            "status",
            "id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    room_no: Mapped[str] = mapped_column(String(64), nullable=False)
    floor: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
