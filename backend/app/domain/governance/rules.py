"""governance domain rules, moved from the flat app.domain modules (pure move)."""

from types import MappingProxyType
from typing import Mapping
from app.domain.identity import ScopeType
from datetime import datetime, timedelta

from .models import (
    AnnouncementStatus,
    AuditAction,
    AuditActionPolicy,
    AuditResourceType,
)

_USER_ROLE_DETAIL_KEYS = frozenset(
    {"target_user_id", "role_code", "scope_type", "source"}
)


_ROLE_PERMISSION_DETAIL_KEYS = frozenset(
    {"role_code", "permission_code", "source"}
)


_ORGANIZATION_TENANT_DETAIL_KEYS = frozenset(
    {"name", "initial_admin_user_id"}
)


_ORGANIZATION_PROPERTY_DETAIL_KEYS = frozenset(
    {"name", "timezone", "initial_admin_user_id"}
)


_RESIDENT_BIND_DETAIL_KEYS = frozenset({"house_id", "relation_type"})


_RESIDENT_APPROVE_DETAIL_KEYS = frozenset(
    {"house_id", "relation_type", "relation_id"}
)


_RESIDENT_RELATION_DETAIL_KEYS = frozenset({"house_id", "relation_type"})


_PROPERTY_BUILDING_DETAIL_KEYS = frozenset(
    {"code", "name", "batch_import"}
)


_PROPERTY_UNIT_DETAIL_KEYS = frozenset(
    {"building_id", "code", "name", "batch_import"}
)


_PROPERTY_HOUSE_DETAIL_KEYS = frozenset(
    {"unit_id", "room_no", "floor", "status", "batch_import"}
)


_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS = frozenset(
    {
        "announcement_id",
        "status",
        "title",
        "content",
        "target_scope_type",
        "building_id",
        "unit_id",
        "audience",
    }
)


_EMPLOYEE_CREATED_DETAIL_KEYS = frozenset({"employee_no", "user_id"})


_EMPLOYEE_UPDATED_DETAIL_KEYS = frozenset(
    {
        "status",
        "department_id",
        "max_capacity",
        "added_duty_ids",
        "added_skill_ids",
    }
)


_EMPLOYEE_CATALOG_DETAIL_KEYS = frozenset({"code", "name"})


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


_WORK_ORDER_RECEIPT_MEDIA_DETAIL_KEYS = frozenset(
    {"work_order_id", "receipt_id", "process_id", "before_count", "after_count"}
)


_WORK_ORDER_DECIDED_DETAIL_KEYS = frozenset(
    {"work_order_id", "status", "process_reason", "reason_code"}
)


_SLA_POLICY_CHANGED_DETAIL_KEYS = frozenset(
    {"scope_type", "property_id", "category", "priority", "before", "after"}
)


AUDIT_ACTION_POLICIES: Mapping[AuditAction, AuditActionPolicy] = MappingProxyType(
    {
        AuditAction.DEV_AUTH_TOKEN_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method", "token_type"}),
            required_detail_keys=frozenset({"auth_method", "token_type"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.WECHAT_AUTH_TOKEN_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method", "token_type"}),
            required_detail_keys=frozenset({"auth_method", "token_type"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_SMS_TOKEN_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method", "token_type"}),
            required_detail_keys=frozenset({"auth_method", "token_type"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_PASSWORD_TOKEN_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method", "token_type"}),
            required_detail_keys=frozenset({"auth_method", "token_type"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_PASSWORD_CHANGED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method"}),
            required_detail_keys=frozenset({"auth_method"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_PASSWORD_RESET: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method"}),
            required_detail_keys=frozenset({"auth_method"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_PHONE_CHANGED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset(
                {"old_phone", "new_phone", "reason_code"}
            ),
            required_detail_keys=frozenset(
                {"old_phone", "new_phone", "reason_code"}
            ),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_SESSION_REVOKED: AuditActionPolicy(
            resource_type=AuditResourceType.AUTH_SESSION,
            allowed_detail_keys=frozenset({"auth_method", "reason_code"}),
            required_detail_keys=frozenset({"auth_method"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_PROFILE_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.USER,
            allowed_detail_keys=frozenset({"auth_method"}),
            required_detail_keys=frozenset(),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_WECHAT_BOUND: AuditActionPolicy(
            resource_type=AuditResourceType.USER_WECHAT,
            allowed_detail_keys=frozenset({"auth_method"}),
            required_detail_keys=frozenset({"auth_method"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.AUTH_WECHAT_REBOUND: AuditActionPolicy(
            resource_type=AuditResourceType.USER_WECHAT,
            allowed_detail_keys=frozenset(
                {"auth_method", "reason_code", "replaced_binding_count"}
            ),
            required_detail_keys=frozenset(
                {"auth_method", "reason_code", "replaced_binding_count"}
            ),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.EMPLOYEE_INVITE_ISSUED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE_INVITE,
            allowed_detail_keys=frozenset({"employee_id"}),
            required_detail_keys=frozenset({"employee_id"}),
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_CLAIMED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE,
            allowed_detail_keys=frozenset({"employee_id", "user_id"}),
            required_detail_keys=frozenset({"employee_id", "user_id"}),
            allowed_scope_types=frozenset({ScopeType.TENANT}),
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
        AuditAction.ORGANIZATION_TENANT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.TENANT,
            allowed_detail_keys=_ORGANIZATION_TENANT_DETAIL_KEYS,
            required_detail_keys=_ORGANIZATION_TENANT_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.ORGANIZATION_PROPERTY_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.PROPERTY,
            allowed_detail_keys=_ORGANIZATION_PROPERTY_DETAIL_KEYS,
            required_detail_keys=_ORGANIZATION_PROPERTY_DETAIL_KEYS,
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
        AuditAction.PROPERTY_BUILDING_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.BUILDING,
            allowed_detail_keys=_PROPERTY_BUILDING_DETAIL_KEYS,
            required_detail_keys=frozenset({"code", "name", "batch_import"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.PROPERTY_BUILDING_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.BUILDING,
            allowed_detail_keys=_PROPERTY_BUILDING_DETAIL_KEYS,
            required_detail_keys=frozenset({"code", "name"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.PROPERTY_UNIT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.UNIT,
            allowed_detail_keys=_PROPERTY_UNIT_DETAIL_KEYS,
            required_detail_keys=frozenset(
                {"building_id", "code", "name", "batch_import"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.PROPERTY_UNIT_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.UNIT,
            allowed_detail_keys=_PROPERTY_UNIT_DETAIL_KEYS,
            required_detail_keys=frozenset({"building_id", "code", "name"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.PROPERTY_HOUSE_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.HOUSE,
            allowed_detail_keys=_PROPERTY_HOUSE_DETAIL_KEYS,
            required_detail_keys=frozenset(
                {"unit_id", "room_no", "floor", "status", "batch_import"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.PROPERTY_HOUSE_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.HOUSE,
            allowed_detail_keys=_PROPERTY_HOUSE_DETAIL_KEYS,
            required_detail_keys=frozenset({"unit_id", "room_no", "floor", "status"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.EMPLOYEE_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE,
            allowed_detail_keys=_EMPLOYEE_CREATED_DETAIL_KEYS,
            required_detail_keys=frozenset({"employee_no"}),
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.EMPLOYEE,
            allowed_detail_keys=_EMPLOYEE_UPDATED_DETAIL_KEYS,
            required_detail_keys=frozenset({"status"}),
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_DUTY_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.DUTY,
            allowed_detail_keys=_EMPLOYEE_CATALOG_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_CATALOG_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.TENANT}),
        ),
        AuditAction.EMPLOYEE_SKILL_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.SKILL,
            allowed_detail_keys=_EMPLOYEE_CATALOG_DETAIL_KEYS,
            required_detail_keys=_EMPLOYEE_CATALOG_DETAIL_KEYS,
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
        AuditAction.WORK_ORDER_CONTACT_READ: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=frozenset({"work_order_id"}),
            required_detail_keys=frozenset({"work_order_id"}),
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
        AuditAction.WORK_ORDER_AI_ANALYSIS_COMPLETED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
            allowed_detail_keys=frozenset(
                {"work_order_id", "analysis_id", "status", "confidence",
                 "dispatch_contract_version", "raw_required_skill",
                 "effective_required_skill", "skill_mapping_applied", "skill_resolution",
                 "event_evidence",
                 "decision_code", "dispatch_fields_applied", "raw_category",
                 "effective_category"}
            ),
            required_detail_keys=frozenset(
                {"work_order_id", "analysis_id", "status"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_AI_ANALYSIS_FAILED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
            allowed_detail_keys=frozenset(
                {"work_order_id", "analysis_id", "status", "error_code",
                 "resource_floor", "forced_retry_actor",
                 "quarantined_since", "previous_work_order_id"}
            ),
            required_detail_keys=frozenset(
                {"work_order_id", "analysis_id", "status", "error_code"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.AI_RECOMMENDATION_REVIEWED: AuditActionPolicy(
            resource_type=AuditResourceType.DISPATCH_RECOMMENDATION,
            allowed_detail_keys=frozenset(
                {
                    "work_order_id",
                    "employee_id",
                    "recommended_employee_id",
                    "overridden",
                }
            ),
            required_detail_keys=frozenset(
                {"work_order_id", "employee_id", "overridden"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.INCIDENT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.INCIDENT,
            allowed_detail_keys=frozenset(
                {"incident_id", "category", "work_order_count"}
            ),
            required_detail_keys=frozenset(
                {"incident_id", "category", "work_order_count"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.INCIDENT_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.INCIDENT,
            allowed_detail_keys=frozenset(
                {
                    "incident_id",
                    "category",
                    "work_order_count",
                    "from_status",
                    "to_status",
                    "reason",
                }
            ),
            required_detail_keys=frozenset(
                {"incident_id", "category", "work_order_count"}
            ),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_LINKED_TO_INCIDENT: AuditActionPolicy(
            resource_type=AuditResourceType.INCIDENT,
            allowed_detail_keys=frozenset({"incident_id", "work_order_id"}),
            required_detail_keys=frozenset({"incident_id", "work_order_id"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ANNOUNCEMENT_DRAFT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.ANNOUNCEMENT,
            allowed_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS
            | frozenset({"ai_draft"}),
            required_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS
            | frozenset({"ai_draft"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.WORK_ORDER_RECEIPT_MEDIA_APPENDED: AuditActionPolicy(
            resource_type=AuditResourceType.WORK_ORDER,
            allowed_detail_keys=_WORK_ORDER_RECEIPT_MEDIA_DETAIL_KEYS,
            required_detail_keys=_WORK_ORDER_RECEIPT_MEDIA_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ANNOUNCEMENT_UPDATED: AuditActionPolicy(
            resource_type=AuditResourceType.ANNOUNCEMENT,
            allowed_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            required_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ANNOUNCEMENT_SUBMITTED: AuditActionPolicy(
            resource_type=AuditResourceType.ANNOUNCEMENT,
            allowed_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            required_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ANNOUNCEMENT_REJECTED: AuditActionPolicy(
            resource_type=AuditResourceType.ANNOUNCEMENT,
            allowed_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS
            | frozenset({"reason"}),
            required_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS
            | frozenset({"reason"}),
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.ANNOUNCEMENT_PUBLISHED: AuditActionPolicy(
            resource_type=AuditResourceType.ANNOUNCEMENT,
            allowed_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            required_detail_keys=_ANNOUNCEMENT_SNAPSHOT_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.PROPERTY}),
        ),
        AuditAction.KNOWLEDGE_DOCUMENT_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            allowed_detail_keys=frozenset(
                {"document_id", "visibility", "document_type"}
            ),
            required_detail_keys=frozenset(
                {"document_id", "visibility", "document_type"}
            ),
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
        AuditAction.KNOWLEDGE_DOCUMENT_VERSION_CREATED: AuditActionPolicy(
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            allowed_detail_keys=frozenset(
                {"document_id", "version", "content_hash", "reactivating_archived"}
            ),
            required_detail_keys=frozenset({"document_id", "version", "content_hash"}),
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
        AuditAction.KNOWLEDGE_DOCUMENT_ACTIVATED: AuditActionPolicy(
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            allowed_detail_keys=frozenset({"document_id", "version", "status"}),
            required_detail_keys=frozenset({"document_id", "version", "status"}),
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
        AuditAction.KNOWLEDGE_DOCUMENT_ARCHIVED: AuditActionPolicy(
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            allowed_detail_keys=frozenset({"document_id", "status"}),
            required_detail_keys=frozenset({"document_id", "status"}),
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
        AuditAction.SLA_POLICY_CHANGED: AuditActionPolicy(
            resource_type=AuditResourceType.SLA_POLICY,
            allowed_detail_keys=_SLA_POLICY_CHANGED_DETAIL_KEYS,
            required_detail_keys=_SLA_POLICY_CHANGED_DETAIL_KEYS,
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
        AuditAction.IDENTITY_RECOVERY_REQUESTED: AuditActionPolicy(
            resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
            allowed_detail_keys=frozenset({"purpose", "status"}),
            required_detail_keys=frozenset({"purpose", "status"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.IDENTITY_RECOVERY_REVIEWED: AuditActionPolicy(
            resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
            allowed_detail_keys=frozenset({"decision", "status"}),
            required_detail_keys=frozenset({"decision", "status"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.IDENTITY_RECOVERY_FINALIZED: AuditActionPolicy(
            resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
            allowed_detail_keys=frozenset({"purpose", "status", "reviewer_count"}),
            required_detail_keys=frozenset({"purpose", "status", "reviewer_count"}),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.IDENTITY_RECOVERY_COMPLETED: AuditActionPolicy(
            resource_type=AuditResourceType.IDENTITY_RECOVERY_CASE,
            allowed_detail_keys=frozenset(
                {
                    "purpose",
                    "status",
                    "source_user_id",
                    "target_user_id",
                    "merge_id",
                    "impact_hash",
                    "moved_counts",
                }
            ),
            required_detail_keys=frozenset(
                {
                    "purpose",
                    "status",
                    "source_user_id",
                    "target_user_id",
                    "merge_id",
                    "impact_hash",
                    "moved_counts",
                }
            ),
            allowed_scope_types=frozenset({ScopeType.SYSTEM}),
        ),
        AuditAction.TASK_INTENT_RETRIED: AuditActionPolicy(
            resource_type=AuditResourceType.TASK_INTENT,
            allowed_detail_keys=frozenset({
                "previous_id",
                "generation",
                "reason_code",
                "source_analysis_id",
                "attachment_count",
            }),
            required_detail_keys=frozenset({"previous_id", "generation", "reason_code"}),
            allowed_scope_types=frozenset({ScopeType.TENANT, ScopeType.PROPERTY}),
        ),
    }
)


DEFAULT_INCIDENT_WINDOW_MINUTES = 10


DEFAULT_INCIDENT_THRESHOLD = 5


def incident_group_key(
    *,
    tenant_id: int,
    property_id: int,
    building_id: int | None,
    category: str,
    subcategory: str | None,
) -> tuple[int, int, int | None, str, str]:
    return (tenant_id, property_id, building_id, category, subcategory or "")


def is_within_incident_window(
    *,
    created_at: datetime,
    anchor: datetime,
    window_minutes: int = DEFAULT_INCIDENT_WINDOW_MINUTES,
) -> bool:
    delta = abs(anchor - created_at)
    return delta <= timedelta(minutes=window_minutes)


def can_transition_announcement(*, current: str, target: str) -> bool:
    allowed = {
        AnnouncementStatus.DRAFT.value: {
            AnnouncementStatus.PENDING_REVIEW.value,
            AnnouncementStatus.REJECTED.value,
        },
        AnnouncementStatus.PENDING_REVIEW.value: {
            AnnouncementStatus.PUBLISHED.value,
            AnnouncementStatus.REJECTED.value,
            AnnouncementStatus.DRAFT.value,
        },
        AnnouncementStatus.PUBLISHED.value: {AnnouncementStatus.ARCHIVED.value},
        AnnouncementStatus.REJECTED.value: {AnnouncementStatus.DRAFT.value},
        AnnouncementStatus.ARCHIVED.value: set(),
    }
    return target in allowed.get(current, set())


STATS_READ = "stats:read"