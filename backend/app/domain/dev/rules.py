"""dev domain rules, moved from the flat app.domain modules (pure move)."""

from types import MappingProxyType
from typing import Mapping
from app.domain.identity import ScopeType

from .models import (
    SeedCodeDefinition,
    SeedUserDefinition,
)

DEV_SEED_LOCK_NAME = "property_ai.dev_seed"


DEV_TENANT_NAME = "__DEV_TENANT__"


DEV_PROPERTY_NAME = "__DEV_PROPERTY__"


DEV_PROPERTY_ADDRESS = "Development Only Property"


DEV_PROPERTY_TIMEZONE = "Asia/Shanghai"


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
    SeedCodeDefinition("property:manage", "Manage property archives"),
    SeedCodeDefinition("resident_binding:create", "Create resident bind request"),
    SeedCodeDefinition("resident_binding:read", "Read resident bind requests"),
    SeedCodeDefinition("resident_binding:review", "Review resident bind requests"),
    SeedCodeDefinition("resident_relation:read", "Read resident house relations"),
    SeedCodeDefinition("employee:read", "Read employees"),
    SeedCodeDefinition("employee:manage", "Manage employees"),
    SeedCodeDefinition("attendance:check_in", "Check in attendance"),
    SeedCodeDefinition("attendance:check_out", "Check out attendance"),
    SeedCodeDefinition("attendance:read", "Read attendance"),
    SeedCodeDefinition("work_order:create", "Create work orders"),
    SeedCodeDefinition("work_order:read", "Read work orders"),
    SeedCodeDefinition("work_order:dispatch", "Dispatch work orders"),
    SeedCodeDefinition("work_order:process", "Process work orders"),
    SeedCodeDefinition("work_order:resolve", "Resolve work orders"),
    SeedCodeDefinition("work_order:decide", "Decide work-order exceptions"),
    SeedCodeDefinition("work_order:cancel", "Cancel work orders"),
    SeedCodeDefinition("ai_analysis:read", "Read AI work-order analysis"),
    SeedCodeDefinition("ai_analysis:review", "Review AI work-order analysis"),
    SeedCodeDefinition(
        "dispatch_recommendation:read",
        "Read dispatch recommendations",
    ),
    SeedCodeDefinition("incident:read", "Read incidents"),
    SeedCodeDefinition("incident:manage", "Manage incidents"),
    SeedCodeDefinition("announcement:read", "Read announcements"),
    SeedCodeDefinition("announcement:draft", "Draft announcements"),
    SeedCodeDefinition("announcement:publish", "Publish announcements"),
    SeedCodeDefinition("knowledge:read", "Read property knowledge"),
    SeedCodeDefinition("knowledge:manage", "Manage property knowledge"),
    SeedCodeDefinition("sla:manage", "Manage SLA policies"),
    SeedCodeDefinition("stats:read", "Read scoped operations statistics"),
)


SEED_ROLE_PERMISSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "SYSTEM_ADMIN": ("role:read", "role:manage", "audit:read"),
        "TENANT_ADMIN": (
            "role:read",
            "role:manage",
            "audit:read",
            "property:read",
            "property:manage",
            "resident_binding:read",
            "resident_binding:review",
            "resident_relation:read",
            "employee:read",
            "employee:manage",
            "attendance:check_in",
            "attendance:check_out",
            "attendance:read",
            "work_order:read",
            "work_order:dispatch",
            "work_order:process",
            "work_order:resolve",
            "work_order:decide",
            "work_order:cancel",
            "ai_analysis:read",
            "ai_analysis:review",
            "dispatch_recommendation:read",
            "incident:read",
            "incident:manage",
            "announcement:read",
            "announcement:draft",
            "announcement:publish",
            "knowledge:read",
            "knowledge:manage",
            "sla:manage",
            "stats:read",
        ),
        "PROPERTY_ADMIN": (
            "role:read",
            "audit:read",
            "property:read",
            "property:manage",
            "resident_binding:read",
            "resident_binding:review",
            "resident_relation:read",
            "employee:read",
            "employee:manage",
            "attendance:check_in",
            "attendance:check_out",
            "attendance:read",
            "work_order:read",
            "work_order:dispatch",
            "work_order:process",
            "work_order:resolve",
            "work_order:decide",
            "work_order:cancel",
            "ai_analysis:read",
            "ai_analysis:review",
            "dispatch_recommendation:read",
            "incident:read",
            "incident:manage",
            "announcement:read",
            "announcement:draft",
            "announcement:publish",
            "knowledge:read",
            "knowledge:manage",
            "sla:manage",
            "stats:read",
        ),
        "SUPERVISOR": (
            "property:read",
            "resident_binding:read",
            "resident_relation:read",
            "employee:read",
            "attendance:check_in",
            "attendance:check_out",
            "attendance:read",
            "work_order:read",
            "work_order:dispatch",
            "work_order:process",
            "work_order:resolve",
            "work_order:decide",
            "work_order:cancel",
            "ai_analysis:read",
            "ai_analysis:review",
            "dispatch_recommendation:read",
            "incident:read",
            "incident:manage",
            "announcement:read",
            "announcement:draft",
            "knowledge:read",
        ),
        "STAFF": (
            "attendance:check_in",
            "attendance:check_out",
            "work_order:read",
            "work_order:process",
            "work_order:resolve",
            "incident:read",
            "announcement:read",
            "knowledge:read",
        ),
        "RESIDENT": (
            "resident_binding:create",
            "resident_binding:read",
            "resident_relation:read",
            "work_order:create",
            "work_order:read",
            "work_order:cancel",
            "announcement:read",
            "knowledge:read",
        ),
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