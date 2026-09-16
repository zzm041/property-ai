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
from app.model.employee import (
    AttendanceRecord,
    Department,
    Duty,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Skill,
)
from app.model.property import Building, House, Property, Tenant, Unit
from app.model.resident import ResidentBindRequest, ResidentHouseRelation

__all__ = [
    "AttendanceRecord",
    "AuditLog",
    "Base",
    "Building",
    "Department",
    "Duty",
    "Employee",
    "EmployeeDailyStatus",
    "EmployeeDuty",
    "EmployeeProperty",
    "EmployeeSkill",
    "House",
    "Permission",
    "Property",
    "ResidentBindRequest",
    "ResidentHouseRelation",
    "Role",
    "RolePermission",
    "Skill",
    "Tenant",
    "Unit",
    "User",
    "UserRole",
]
