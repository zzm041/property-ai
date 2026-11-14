"""dev domain rules, moved from the flat app.domain modules (pure move)."""

import os

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
            # F-4 / t210: 主管就是现场审核人。没有这条权限时，公告能提审却永远停
            # 在 PENDING_REVIEW（页面的「人工审核通过并发布」只在 canPublish 时渲染）。
            "announcement:publish",
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


# PA-196: 超级管理员角色（SUPERVISOR, role_id=3）之前只有角色与权限、没有任何用户持有它，
# 所以 /api/v1/supervisor/work-orders/{id}/analyze 对所有人返回 IAM.FORBIDDEN。演示号码用 139 段，
# 密码走与其他入口相同的 argon2id 流程（app.core.passwords.hash_password）。
DEV_SUPERVISOR_PHONE = "+8613910000001"
DEV_SUPERVISOR_PASSWORD = os.environ.get("DEV_SUPERVISOR_PASSWORD", "change-me-dev-only")
# The demo tenant/property that owns the work orders a reviewer actually looks at.
# When a database has these rows the supervisor grant lands there; otherwise the
# seed falls back to the tenant/property it creates itself.
DEV_SUPERVISOR_TENANT_ID = 5
DEV_SUPERVISOR_PROPERTY_ID = 5

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
    SeedUserDefinition(
        "__DEV_SUPERVISOR__",
        "SUPERVISOR",
        ScopeType.PROPERTY,
        phone_e164=DEV_SUPERVISOR_PHONE,
        password=DEV_SUPERVISOR_PASSWORD,
        scope_tenant_id=DEV_SUPERVISOR_TENANT_ID,
        scope_property_id=DEV_SUPERVISOR_PROPERTY_ID,
    ),
)