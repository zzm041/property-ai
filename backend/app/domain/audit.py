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


class AuditResourceType(StrEnum):
    USER = "USER"
    USER_ROLE = "USER_ROLE"
    ROLE_PERMISSION = "ROLE_PERMISSION"


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
    }
)
