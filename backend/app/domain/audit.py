"""Pure append-only audit event definitions and action policies."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from app.domain.iam import OrganizationScope, ScopeType


class AuditAction(StrEnum):
    DEV_AUTH_TOKEN_ISSUED = "DEV_AUTH_TOKEN_ISSUED"
    IAM_USER_ROLE_GRANTED = "IAM_USER_ROLE_GRANTED"
    IAM_USER_ROLE_REVOKED = "IAM_USER_ROLE_REVOKED"
    IAM_ROLE_PERMISSION_GRANTED = "IAM_ROLE_PERMISSION_GRANTED"
    IAM_ROLE_PERMISSION_REVOKED = "IAM_ROLE_PERMISSION_REVOKED"
    DEV_SEED_TENANT_CREATED = "DEV_SEED_TENANT_CREATED"
    DEV_SEED_PROPERTY_CREATED = "DEV_SEED_PROPERTY_CREATED"
    DEV_SEED_USER_CREATED = "DEV_SEED_USER_CREATED"
    DEV_SEED_ROLE_CREATED = "DEV_SEED_ROLE_CREATED"
    DEV_SEED_PERMISSION_CREATED = "DEV_SEED_PERMISSION_CREATED"
    RESIDENT_BIND_REQUEST_CREATED = "RESIDENT_BIND_REQUEST_CREATED"
    RESIDENT_BIND_REQUEST_APPROVED = "RESIDENT_BIND_REQUEST_APPROVED"
    RESIDENT_BIND_REQUEST_REJECTED = "RESIDENT_BIND_REQUEST_REJECTED"
    RESIDENT_HOUSE_RELATION_ENDED = "RESIDENT_HOUSE_RELATION_ENDED"
    EMPLOYEE_CREATED = "EMPLOYEE_CREATED"
    EMPLOYEE_UPDATED = "EMPLOYEE_UPDATED"
    EMPLOYEE_PROPERTY_ASSIGNED = "EMPLOYEE_PROPERTY_ASSIGNED"
    EMPLOYEE_PROPERTY_ENDED = "EMPLOYEE_PROPERTY_ENDED"
    ATTENDANCE_CHECKED_IN = "ATTENDANCE_CHECKED_IN"
    ATTENDANCE_CHECKED_OUT = "ATTENDANCE_CHECKED_OUT"
    ATTENDANCE_STATUS_UPDATED = "ATTENDANCE_STATUS_UPDATED"
    WORK_ORDER_CREATED = "WORK_ORDER_CREATED"
    WORK_ORDER_ASSIGNED = "WORK_ORDER_ASSIGNED"
    WORK_ORDER_REASSIGNED = "WORK_ORDER_REASSIGNED"
    WORK_ORDER_PROCESS_STARTED = "WORK_ORDER_PROCESS_STARTED"
    WORK_ORDER_PROCESS_UPDATED = "WORK_ORDER_PROCESS_UPDATED"
    WORK_ORDER_RESOLVED = "WORK_ORDER_RESOLVED"
    WORK_ORDER_CLOSED = "WORK_ORDER_CLOSED"
    WORK_ORDER_REOPENED = "WORK_ORDER_REOPENED"
    WORK_ORDER_CANCELLED = "WORK_ORDER_CANCELLED"
    WORK_ORDER_ACCEPTED = "WORK_ORDER_ACCEPTED"
    WORK_ORDER_REJECTED = "WORK_ORDER_REJECTED"
    WORK_ORDER_DECIDED = "WORK_ORDER_DECIDED"


class AuditResourceType(StrEnum):
    USER = "USER"
    USER_ROLE = "USER_ROLE"
    ROLE_PERMISSION = "ROLE_PERMISSION"
    TENANT = "TENANT"
    PROPERTY = "PROPERTY"
    ROLE = "ROLE"
    PERMISSION = "PERMISSION"
    RESIDENT_BIND_REQUEST = "RESIDENT_BIND_REQUEST"
    RESIDENT_HOUSE_RELATION = "RESIDENT_HOUSE_RELATION"
    EMPLOYEE = "EMPLOYEE"
    EMPLOYEE_PROPERTY = "EMPLOYEE_PROPERTY"
    ATTENDANCE_RECORD = "ATTENDANCE_RECORD"
    EMPLOYEE_DAILY_STATUS = "EMPLOYEE_DAILY_STATUS"
    WORK_ORDER = "WORK_ORDER"
    WORK_ORDER_ASSIGNMENT = "WORK_ORDER_ASSIGNMENT"


class AuditSource(StrEnum):
    INTERNAL = "INTERNAL"
    DEVELOPMENT_SEED = "DEVELOPMENT_SEED"


@dataclass(frozen=True, slots=True)
class AuditActionPolicy:
    resource_type: AuditResourceType
    allowed_detail_keys: frozenset[str]
    required_detail_keys: frozenset[str]
    allowed_scope_types: frozenset[ScopeType]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: AuditAction
    resource_type: AuditResourceType
    request_id: str
    scope: OrganizationScope
    actor_user_id: int | None = None
    resource_id: int | None = None
    details: Mapping[str, object] | None = None


_USER_ROLE_DETAIL_KEYS = frozenset(
    {"target_user_id", "role_code", "scope_type", "source"}
)
_ROLE_PERMISSION_DETAIL_KEYS = frozenset(
    {"role_code", "permission_code", "source"}
)
_RESIDENT_BIND_DETAIL_KEYS = frozenset({"house_id", "relation_type"})
_RESIDENT_APPROVE_DETAIL_KEYS = frozenset(
    {"house_id", "relation_type", "relation_id"}
)
_RESIDENT_RELATION_DETAIL_KEYS = frozenset({"house_id", "relation_type"})
_EMPLOYEE_CREATED_DETAIL_KEYS = frozenset({"employee_no", "user_id"})
_EMPLOYEE_UPDATED_DETAIL_KEYS = frozenset({"status"})
_EMPLOYEE_PROPERTY_DETAIL_KEYS = frozenset({"employee_id", "property_id"})
_ATTENDANCE_DETAIL_KEYS = frozenset(
    {"employee_id", "property_id", "business_date"}
)
_ATTENDANCE_STATUS_DETAIL_KEYS = frozenset(
    {"employee_id", "property_id", "business_date", "on_duty"}
)
_WORK_ORDER_CREATED_DETAIL_KEYS = frozenset(
    {"work_order_id", "status", "priority", "category"}
)
_WORK_ORDER_ASSIGNED_DETAIL_KEYS = frozenset(
    {"work_order_id", "employee_id", "status"}
)
_WORK_ORDER_REASSIGNED_DETAIL_KEYS = frozenset(
    {"work_order_id", "employee_id", "status", "reason_code"}
)
_WORK_ORDER_STATUS_DETAIL_KEYS = frozenset(
    {"work_order_id", "status", "reason_code"}
)
_WORK_ORDER_PROCESS_DETAIL_KEYS = frozenset(
    {"work_order_id", "status", "process_reason"}
)
_WORK_ORDER_DECIDED_DETAIL_KEYS = frozenset(
    {"work_order_id", "status", "process_reason", "reason_code"}
)

AUDIT_ACTION_POLICIES: Mapping[AuditAction, AuditActionPolicy] = MappingProxyType(
    {
        AuditAction.DEV_AUTH_TOKEN_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method", "token_type"}),
            required_detail_keys=frozenset({"auth_method", "token_type"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.IAM_USER_ROLE_GRANTED: AuditActionPolicy(
            resource_type=AuditResourceType.USER_ROLE,
            allowed_detail_keys=_USER_ROLE_DETAIL_KEYS,
            required_detail_keys=_USER_ROLE_DETAIL_KEYS,
            allowed_scope_types=frozenset(ScopeType),
        ),
        AuditAction.IAM_USER_ROLE_REVOKED: AuditActionPolicy(
            resource_type=AuditResourceType.USER_ROLE,
            allowed_detail_keys=_USER_ROLE_DETAIL_KEYS,
            required_detail_keys=_USER_ROLE_DETAIL_KEYS,
            allowed_scope_types=frozenset(ScopeType),
        ),
        AuditAction.IAM_ROLE_PERMISSION_GRANTED: AuditActionPolicy(
            resource_type=AuditResourceType.ROLE_PERMISSION,
            allowed_detail_keys=_ROLE_PERMISSION_DETAIL_KEYS,
            required_detail_keys=_ROLE_PERMISSION_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.IAM_ROLE_PERMISSION_REVOKED: AuditActionPolicy(
            resource_type=AuditResourceType.ROLE_PERMISSION,
            allowed_detail_keys=_ROLE_PERMISSION_DETAIL_KEYS,
            required_detail_keys=_ROLE_PERMISSION_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.DEV_SEED_TENANT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.TENANT,
            allowed_detail_keys=frozenset({"seed_key", "source"}),
            required_detail_keys=frozenset({"seed_key", "source"}),
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.DEV_SEED_PROPERTY_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.PROPERTY,
            allowed_detail_keys=frozenset({"seed_key", "source"}),
            required_detail_keys=frozenset({"seed_key", "source"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.DEV_SEED_USER_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"seed_key", "source"}),
            required_detail_keys=frozenset({"seed_key", "source"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.DEV_SEED_ROLE_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.ROLE,
            allowed_detail_keys=frozenset({"role_code", "source"}),
            required_detail_keys=frozenset({"role_code", "source"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.DEV_SEED_PERMISSION_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.PERMISSION,
            allowed_detail_keys=frozenset({"permission_code", "source"}),
            required_detail_keys=frozenset({"permission_code", "source"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.RESIDENT_BIND_REQUEST_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            allowed_detail_keys=_RESIDENT_BIND_DETAIL_KEYS,
            required_detail_keys=_RESIDENT_BIND_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.RESIDENT_BIND_REQUEST_APPROVED: AuditActionPolicy(
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            allowed_detail_keys=_RESIDENT_APPROVE_DETAIL_KEYS,
            required_detail_keys=_RESIDENT_APPROVE_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.RESIDENT_BIND_REQUEST_REJECTED: AuditActionPolicy(
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            allowed_detail_keys=_RESIDENT_BIND_DETAIL_KEYS,
            required_detail_keys=_RESIDENT_BIND_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.RESIDENT_HOUSE_RELATION_ENDED: AuditActionPolicy(
            resource_type=AuditResourceType.RESIDENT_HOUSE_RELATION,
            allowed_detail_keys=_RESIDENT_RELATION_DETAIL_KEYS,
            required_detail_keys=_RESIDENT_RELATION_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.EMPLOYEE_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE,
            allowed_detail_keys=_EMPLOYEE_CREATED_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_CREATED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE,
            allowed_detail_keys=_EMPLOYEE_UPDATED_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_UPDATED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_PROPERTY_ASSIGNED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE_PROPERTY,
            allowed_detail_keys=_EMPLOYEE_PROPERTY_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_PROPERTY_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.EMPLOYEE_PROPERTY_ENDED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE_PROPERTY,
            allowed_detail_keys=_EMPLOYEE_PROPERTY_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_PROPERTY_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ATTENDANCE_CHECKED_IN: AuditActionPolicy(
            resource_type=AuditResourceType.ATTENDANCE_RECORD,
            allowed_detail_keys=_ATTENDANCE_DETAIL_KEYS,
            required_detail_keys=_ATTENDANCE_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ATTENDANCE_CHECKED_OUT: AuditActionPolicy(
            resource_type=AuditResourceType.ATTENDANCE_RECORD,
            allowed_detail_keys=_ATTENDANCE_DETAIL_KEYS,
            required_detail_keys=_ATTENDANCE_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ATTENDANCE_STATUS_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE_DAILY_STATUS,
            allowed_detail_keys=_ATTENDANCE_STATUS_DETAIL_KEYS,
            required_detail_keys=_ATTENDANCE_STATUS_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_CREATED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_CREATED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_ASSIGNED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            allowed_detail_keys=_WORK_ORDER_ASSIGNED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_ASSIGNED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_REASSIGNED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            allowed_detail_keys=_WORK_ORDER_REASSIGNED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_REASSIGNED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_PROCESS_STARTED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            required_detail_keys=frozenset({"work_order_id", "status"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_PROCESS_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_PROCESS_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_PROCESS_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_RESOLVED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            required_detail_keys=frozenset({"work_order_id", "status"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_CLOSED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            required_detail_keys=frozenset({"work_order_id", "status"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_REOPENED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_CANCELLED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_STATUS_DETAIL_KEYS,
            required_detail_keys=frozenset({"work_order_id", "status"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_ACCEPTED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_ASSIGNED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_ASSIGNED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_REJECTED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_REASSIGNED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_REASSIGNED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_DECIDED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_DECIDED_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_DECIDED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
    }
)
