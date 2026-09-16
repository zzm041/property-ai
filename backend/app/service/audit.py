"""Validation and append-only audit writing within caller-owned transactions."""

from collections.abc import Mapping, Sequence
import re

from sqlalchemy.orm import Session

from app.core.errors import AppError, AuditErrorCode
from app.domain.audit import (
    AUDIT_ACTION_POLICIES,
    AuditAction,
    AuditEvent,
    AuditResourceType,
    AuditSource,
)
from app.domain.iam import OrganizationScope, ScopeType
from app.model.identity import AuditLog
from app.repository.audit import AuditRepository

_ROLE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_PERMISSION_CODE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
)
_ALLOWED_SOURCES = frozenset(source.value for source in AuditSource)
_SEED_KEY_PATTERN = re.compile(r"^__[A-Z0-9_]+__$")
_SENSITIVE_KEY_MARKERS = frozenset(
    {
        "jwt",
        "jwttoken",
        "jti",
        "token",
        "accesstoken",
        "refreshtoken",
        "authorization",
        "authorizationheader",
        "cookie",
        "appsecretkey",
        "secret",
        "apikey",
        "password",
        "passwordhash",
        "smscode",
        "idcard",
        "idnumber",
        "identitynumber",
        "credential",
        "credentials",
        "requestbody",
        "rawrequestbody",
        "rawpayload",
    }
)


class AuditService:
    """Validate and flush audit records without committing or rolling back."""

    def __init__(
        self,
        session: Session,
        repository: AuditRepository | None = None,
    ) -> None:
        self._repository = repository or AuditRepository(session)

    def append(
        self,
        *,
        action: AuditAction | str,
        resource_type: AuditResourceType | str,
        request_id: str,
        scope_type: ScopeType | str,
        actor_user_id: int | None = None,
        tenant_id: int | None = None,
        property_id: int | None = None,
        resource_id: int | None = None,
        details: Mapping[str, object] | None = None,
    ) -> AuditLog:
        validated_action = self._validate_action(action)
        validated_resource_type = self._validate_resource_type(resource_type)
        validated_request_id = self._validate_request_id(request_id)
        self._validate_optional_id(actor_user_id, "actor_user_id")
        self._validate_optional_id(resource_id, "resource_id")
        if resource_id is None:
            self._raise_invalid_event("resource_id is required")
        scope = self._validate_scope(
            scope_type=scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
        )
        validated_details = self._validate_details(
            action=validated_action,
            resource_type=validated_resource_type,
            scope=scope,
            details=details,
        )
        event = AuditEvent(
            action=validated_action,
            resource_type=validated_resource_type,
            request_id=validated_request_id,
            scope=scope,
            actor_user_id=actor_user_id,
            resource_id=resource_id,
            details=validated_details,
        )
        return self._repository.append(self._to_model(event))

    @staticmethod
    def _to_model(event: AuditEvent) -> AuditLog:
        return AuditLog(
            actor_user_id=event.actor_user_id,
            tenant_id=event.scope.tenant_id,
            property_id=event.scope.property_id,
            action=event.action.value,
            resource_type=event.resource_type.value,
            resource_id=event.resource_id,
            request_id=event.request_id,
            details_json=dict(event.details) if event.details else None,
        )

    @classmethod
    def _validate_action(cls, action: AuditAction | str) -> AuditAction:
        try:
            return AuditAction(action)
        except (TypeError, ValueError) as exc:
            cls._raise_invalid_event("action is not supported", cause=exc)

    @classmethod
    def _validate_resource_type(
        cls,
        resource_type: AuditResourceType | str,
    ) -> AuditResourceType:
        try:
            return AuditResourceType(resource_type)
        except (TypeError, ValueError) as exc:
            cls._raise_invalid_event("resource_type is not supported", cause=exc)

    @classmethod
    def _validate_request_id(cls, request_id: str) -> str:
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id != request_id.strip()
            or len(request_id) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in request_id)
        ):
            cls._raise_invalid_event("request_id is invalid")
        return request_id

    @classmethod
    def _validate_scope(
        cls,
        *,
        scope_type: ScopeType | str,
        tenant_id: int | None,
        property_id: int | None,
    ) -> OrganizationScope:
        cls._validate_optional_id(tenant_id, "tenant_id")
        cls._validate_optional_id(property_id, "property_id")
        try:
            return OrganizationScope(
                scope_type=ScopeType(scope_type),
                tenant_id=tenant_id,
                property_id=property_id,
            )
        except (TypeError, ValueError) as exc:
            raise AppError(
                code=AuditErrorCode.INVALID_SCOPE,
                message="Audit scope is invalid",
                status_code=422,
            ) from exc

    @classmethod
    def _validate_details(
        cls,
        *,
        action: AuditAction,
        resource_type: AuditResourceType,
        scope: OrganizationScope,
        details: Mapping[str, object] | None,
    ) -> dict[str, object]:
        policy = AUDIT_ACTION_POLICIES[action]
        if resource_type is not policy.resource_type:
            cls._raise_invalid_event(
                "resource_type does not match the audit action"
            )
        if scope.scope_type not in policy.allowed_scope_types:
            raise AppError(
                code=AuditErrorCode.INVALID_SCOPE,
                message="Audit action is not allowed in this scope",
                status_code=422,
            )

        candidate: Mapping[str, object] = {} if details is None else details
        if not isinstance(candidate, Mapping):
            cls._raise_invalid_details("details must be an object")
        cls._reject_sensitive_keys(candidate)

        keys = set(candidate)
        if not all(isinstance(key, str) for key in keys):
            cls._raise_invalid_details("details keys must be strings")
        unexpected = keys - policy.allowed_detail_keys
        missing = policy.required_detail_keys - keys
        if unexpected:
            cls._raise_invalid_details("details contains fields not allowed for action")
        if missing:
            cls._raise_invalid_details("details is missing required fields")

        sanitized = dict(candidate)
        cls._validate_detail_values(sanitized)
        scope_value = sanitized.get("scope_type")
        if scope_value is not None and scope_value != scope.scope_type.value:
            cls._raise_invalid_details("details scope_type does not match audit scope")
        return sanitized

    @classmethod
    def _validate_detail_values(cls, details: dict[str, object]) -> None:
        for key, value in details.items():
            if key == "auth_method" and value != "development":
                cls._raise_invalid_details("auth_method is invalid")
            elif key == "token_type" and value != "access":
                cls._raise_invalid_details("token_type is invalid")
            elif key == "target_user_id":
                if not cls._is_positive_id(value):
                    cls._raise_invalid_details("target_user_id is invalid")
            elif key == "role_code":
                if not isinstance(value, str) or not _ROLE_CODE_PATTERN.fullmatch(
                    value
                ):
                    cls._raise_invalid_details("role_code is invalid")
            elif key == "permission_code":
                if (
                    not isinstance(value, str)
                    or not _PERMISSION_CODE_PATTERN.fullmatch(value)
                ):
                    cls._raise_invalid_details("permission_code is invalid")
            elif key == "scope_type":
                if value not in {scope.value for scope in ScopeType}:
                    cls._raise_invalid_details("scope_type is invalid")
            elif key == "source" and value not in _ALLOWED_SOURCES:
                cls._raise_invalid_details("source is invalid")
            elif key == "seed_key":
                if (
                    not isinstance(value, str)
                    or len(value) > 200
                    or not _SEED_KEY_PATTERN.fullmatch(value)
                ):
                    cls._raise_invalid_details("seed_key is invalid")

    @classmethod
    def _reject_sensitive_keys(cls, value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested_value in value.items():
                if not isinstance(key, str):
                    cls._raise_invalid_details("details keys must be strings")
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if cls._is_sensitive_key(normalized):
                    cls._raise_invalid_details(
                        "details contains a forbidden sensitive field"
                    )
                cls._reject_sensitive_keys(nested_value)
        elif isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            for item in value:
                cls._reject_sensitive_keys(item)

    @staticmethod
    def _is_sensitive_key(normalized_key: str) -> bool:
        if normalized_key in _SENSITIVE_KEY_MARKERS:
            return True
        return any(
            normalized_key.endswith(marker)
            for marker in _SENSITIVE_KEY_MARKERS
        )

    @classmethod
    def _validate_optional_id(cls, value: int | None, field: str) -> None:
        if value is not None and not cls._is_positive_id(value):
            cls._raise_invalid_event(f"{field} must be a positive integer")

    @staticmethod
    def _is_positive_id(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _raise_invalid_event(
        message: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        error = AppError(
            code=AuditErrorCode.INVALID_EVENT,
            message=message,
            status_code=422,
        )
        if cause is not None:
            raise error from cause
        raise error

    @staticmethod
    def _raise_invalid_details(message: str) -> None:
        raise AppError(
            code=AuditErrorCode.INVALID_DETAILS,
            message=message,
            status_code=422,
        )
