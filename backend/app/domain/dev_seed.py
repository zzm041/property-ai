"""Pure Development Seed definitions for the Task 3 foundation."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.domain.iam import ScopeType

DEV_SEED_LOCK_NAME = "property_ai.dev_seed"
DEV_TENANT_NAME = "__DEV_TENANT__"
DEV_PROPERTY_NAME = "__DEV_PROPERTY__"
DEV_PROPERTY_ADDRESS = "Development Only Property"
DEV_PROPERTY_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class SeedCodeDefinition:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class SeedUserDefinition:
    display_name: str
    role_code: str
    scope_type: ScopeType


SEED_ROLES = (
    SeedCodeDefinition("RESIDENT", "Resident"),
    SeedCodeDefinition("STAFF", "Staff"),
    SeedCodeDefinition("SUPERVISOR", "Supervisor"),
    SeedCodeDefinition("PROPERTY_ADMIN", "Property Admin"),
    SeedCodeDefinition("TENANT_ADMIN", "Tenant Admin"),
    SeedCodeDefinition("SYSTEM_ADMIN", "System Admin"),
)

SEED_PERMISSIONS = (
    SeedCodeDefinition("role:read", "Read roles"),
    SeedCodeDefinition("role:manage", "Manage roles"),
    SeedCodeDefinition("audit:read", "Read audit"),
    SeedCodeDefinition("property:read", "Read property"),
)

SEED_ROLE_PERMISSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "SYSTEM_ADMIN": ("role:read", "role:manage", "audit:read"),
        "TENANT_ADMIN": (
            "role:read",
            "role:manage",
            "audit:read",
            "property:read",
        ),
        "PROPERTY_ADMIN": ("role:read", "audit:read", "property:read"),
        "SUPERVISOR": ("property:read",),
        "STAFF": (),
        "RESIDENT": (),
    }
)

SEED_USERS = (
    SeedUserDefinition(
        "__DEV_SYSTEM_ADMIN__",
        "SYSTEM_ADMIN",
        ScopeType.SYSTEM,
    ),
    SeedUserDefinition(
        "__DEV_TENANT_ADMIN__",
        "TENANT_ADMIN",
        ScopeType.TENANT,
    ),
    SeedUserDefinition(
        "__DEV_PROPERTY_MANAGER__",
        "PROPERTY_ADMIN",
        ScopeType.PROPERTY,
    ),
    SeedUserDefinition(
        "__DEV_RESIDENT__",
        "RESIDENT",
        ScopeType.PROPERTY,
    ),
    SeedUserDefinition(
        "__DEV_STAFF__",
        "STAFF",
        ScopeType.PROPERTY,
    ),
)
