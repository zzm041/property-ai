"""SQLAlchemy 2.x models. Schema changes are managed only by Alembic."""

from app.model.base import Base
from app.model.property import Building, House, Property, Tenant, Unit

__all__ = ["Base", "Building", "House", "Property", "Tenant", "Unit"]
