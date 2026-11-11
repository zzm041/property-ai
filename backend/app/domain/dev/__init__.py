"""Development seed fixtures. Runtime code never imports this package.

The historical flat modules re-export this package as compatibility shims.

This domain has no errors of its own: everything uses the app.core.errors families
instead of defining a parallel hierarchy.
"""

from .models import (
    SeedCodeDefinition,
    SeedUserDefinition,
)
from .rules import (
    DEV_PROPERTY_ADDRESS,
    DEV_PROPERTY_NAME,
    DEV_PROPERTY_TIMEZONE,
    DEV_SEED_LOCK_NAME,
    DEV_TENANT_NAME,
    SEED_PERMISSIONS,
    SEED_ROLES,
    SEED_ROLE_PERMISSIONS,
    SEED_USERS,
)

__all__ = [
    "DEV_PROPERTY_ADDRESS",
    "DEV_PROPERTY_NAME",
    "DEV_PROPERTY_TIMEZONE",
    "DEV_SEED_LOCK_NAME",
    "DEV_TENANT_NAME",
    "SEED_PERMISSIONS",
    "SEED_ROLES",
    "SEED_ROLE_PERMISSIONS",
    "SEED_USERS",
    "SeedCodeDefinition",
    "SeedUserDefinition",
]
