"""SQLAlchemy 2.x models. Schema changes are managed only by Alembic."""

from app.model.base import Base
from app.model.identity import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.model.property import Building, House, Property, Tenant, Unit

__all__ = [
    "AuditLog",
    "Base",
    "Building",
    "House",
    "Permission",
    "Property",
    "Role",
    "RolePermission",
    "Tenant",
    "Unit",
    "User",
    "UserRole",
]
