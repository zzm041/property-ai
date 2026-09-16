"""Audit event validation, allowlist, and transaction participation tests."""

from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, AuditErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import ScopeType
from app.model.identity import AuditLog
from app.repository.audit import AuditRepository
from app.service.audit import AuditService


def _service() -> tuple[AuditService, MagicMock, MagicMock]:
    session = MagicMock()
    repository = MagicMock(spec=AuditRepository)
    repository.append.side_effect = lambda audit_log: audit_log
    return AuditService(session, repository), session, repository


def _user_role_details(
    *,
    scope_type: str = "PROPERTY",
) -> dict[str, object]:
    return {
        "target_user_id": 22,
        "role_code": "PROPERTY_ADMIN",
        "scope_type": scope_type,
        "source": "INTERNAL",
    }


def test_valid_audit_event_is_constructed_and_flushed() -> None:
    service, session, repository = _service()

    result = service.append(
        action=AuditAction.IAM_USER_ROLE_GRANTED,
        resource_type=AuditResourceType.USER_ROLE,
        actor_user_id=11,
        resource_id=33,
        request_id="c1-valid-request",
        scope_type=ScopeType.PROPERTY,
        tenant_id=1,
        property_id=2,
        details=_user_role_details(),
    )

    assert isinstance(result, AuditLog)
    assert result.actor_user_id == 11
    assert result.resource_id == 33
    assert result.request_id == "c1-valid-request"
    assert result.tenant_id == 1
    assert result.property_id == 2
    assert result.details_json == _user_role_details()
    repository.append.assert_called_once_with(result)
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("request_id", ["", " ", "\n", "x" * 129])
def test_request_id_is_required_and_bounded(request_id: str) -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER,
            actor_user_id=1,
            resource_id=1,
            request_id=request_id,
            scope_type=ScopeType.SYSTEM,
            details={"auth_method": "development", "token_type": "access"},
        )

    assert captured.value.code == AuditErrorCode.INVALID_EVENT
    repository.append.assert_not_called()


@pytest.mark.parametrize(
    ("scope_type", "tenant_id", "property_id"),
    [
        (ScopeType.SYSTEM, 1, None),
        (ScopeType.SYSTEM, None, 1),
        (ScopeType.TENANT, None, None),
        (ScopeType.TENANT, 1, 2),
        (ScopeType.PROPERTY, 1, None),
        (ScopeType.PROPERTY, None, 2),
        ("SELF", None, None),
    ],
)
def test_invalid_scope_shape_is_rejected(
    scope_type: ScopeType | str,
    tenant_id: int | None,
    property_id: int | None,
) -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.IAM_USER_ROLE_GRANTED,
            resource_type=AuditResourceType.USER_ROLE,
            actor_user_id=1,
            resource_id=2,
            request_id="invalid-scope",
            scope_type=scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
            details=_user_role_details(scope_type=str(scope_type)),
        )

    assert captured.value.code == AuditErrorCode.INVALID_SCOPE
    repository.append.assert_not_called()


def test_action_specific_allowlist_rejects_unexpected_field() -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER,
            actor_user_id=1,
            resource_id=1,
            request_id="unexpected-details",
            scope_type=ScopeType.SYSTEM,
            details={
                "auth_method": "development",
                "token_type": "access",
                "note": "not allowed",
            },
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS
    repository.append.assert_not_called()


@pytest.mark.parametrize(
    "details",
    [
        {
            "auth_method": "development",
            "token_type": "access",
            "authorization": "Bearer secret",
        },
        {
            "auth_method": "development",
            "token_type": "access",
            "unexpected": {"inner": {"password_hash": "secret"}},
        },
        {
            "auth_method": "development",
            "token_type": "access",
            "unexpected": [{"APP_SECRET_KEY": "secret"}],
        },
        {
            "auth_method": "development",
            "token_type": "access",
            "unexpected": {"provider": {"client_secret": "secret"}},
        },
        {
            "auth_method": "development",
            "token_type": "access",
            "raw_request_body": {"safe": "looks harmless"},
        },
    ],
)
def test_sensitive_keys_are_recursively_rejected(
    details: dict[str, object],
) -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER,
            actor_user_id=1,
            resource_id=1,
            request_id="sensitive-details",
            scope_type=ScopeType.SYSTEM,
            details=details,
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS
    assert "sensitive" in captured.value.message
    repository.append.assert_not_called()


def test_allowlisted_detail_values_are_constrained() -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER,
            actor_user_id=1,
            resource_id=1,
            request_id="invalid-value",
            scope_type=ScopeType.SYSTEM,
            details={"auth_method": "development", "token_type": "raw-jwt"},
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS
    repository.append.assert_not_called()


def test_action_resource_and_scope_policy_are_enforced() -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as resource_error:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER_ROLE,
            actor_user_id=1,
            resource_id=1,
            request_id="resource-mismatch",
            scope_type=ScopeType.SYSTEM,
            details={"auth_method": "development", "token_type": "access"},
        )
    with pytest.raises(AppError) as scope_error:
        service.append(
            action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
            resource_type=AuditResourceType.USER,
            actor_user_id=1,
            resource_id=1,
            request_id="scope-mismatch",
            scope_type=ScopeType.TENANT,
            tenant_id=1,
            details={"auth_method": "development", "token_type": "access"},
        )

    assert resource_error.value.code == AuditErrorCode.INVALID_EVENT
    assert scope_error.value.code == AuditErrorCode.INVALID_SCOPE
    repository.append.assert_not_called()


def test_details_scope_type_must_match_event_scope() -> None:
    service, _, repository = _service()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.IAM_USER_ROLE_GRANTED,
            resource_type=AuditResourceType.USER_ROLE,
            actor_user_id=1,
            resource_id=2,
            request_id="scope-details-mismatch",
            scope_type=ScopeType.TENANT,
            tenant_id=1,
            details=_user_role_details(scope_type="PROPERTY"),
        )

    assert captured.value.code == AuditErrorCode.INVALID_DETAILS
    repository.append.assert_not_called()


def test_seed_audit_allows_null_actor_and_rejects_invalid_seed_key() -> None:
    service, _, repository = _service()

    result = service.append(
        action=AuditAction.DEV_SEED_TENANT_CREATED,
        resource_type=AuditResourceType.TENANT,
        actor_user_id=None,
        resource_id=11,
        request_id="c4-seed-tenant",
        scope_type=ScopeType.TENANT,
        tenant_id=11,
        details={"seed_key": "__DEV_TENANT__", "source": "DEVELOPMENT_SEED"},
    )

    assert result.actor_user_id is None
    assert result.details_json == {
        "seed_key": "__DEV_TENANT__",
        "source": "DEVELOPMENT_SEED",
    }
    repository.append.assert_called_once()

    with pytest.raises(AppError) as captured:
        service.append(
            action=AuditAction.DEV_SEED_TENANT_CREATED,
            resource_type=AuditResourceType.TENANT,
            resource_id=11,
            request_id="c4-bad-seed-key",
            scope_type=ScopeType.TENANT,
            tenant_id=11,
            details={"seed_key": "not-a-marker", "source": "DEVELOPMENT_SEED"},
        )
    assert captured.value.code == AuditErrorCode.INVALID_DETAILS


def test_jti_and_cookie_keys_are_rejected() -> None:
    service, _, repository = _service()

    for details in (
        {
            "auth_method": "development",
            "token_type": "access",
            "jti": "must-not-be-audited",
        },
        {
            "auth_method": "development",
            "token_type": "access",
            "cookie": "session=secret",
        },
    ):
        with pytest.raises(AppError) as captured:
            service.append(
                action=AuditAction.DEV_AUTH_TOKEN_ISSUED,
                resource_type=AuditResourceType.USER,
                actor_user_id=1,
                resource_id=1,
                request_id="c3-forbidden-key",
                scope_type=ScopeType.SYSTEM,
                details=details,
            )
        assert captured.value.code == AuditErrorCode.INVALID_DETAILS
    repository.append.assert_not_called()


def test_resident_bind_audit_allowlist_and_scope() -> None:
    service, _, repository = _service()

    created = service.append(
        action=AuditAction.RESIDENT_BIND_REQUEST_CREATED,
        resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
        actor_user_id=11,
        resource_id=44,
        request_id="t4-audit-create",
        scope_type=ScopeType.PROPERTY,
        tenant_id=1,
        property_id=2,
        details={"house_id": 9, "relation_type": "OWNER"},
    )
    assert created.details_json == {"house_id": 9, "relation_type": "OWNER"}

    with pytest.raises(AppError) as reason_error:
        service.append(
            action=AuditAction.RESIDENT_BIND_REQUEST_REJECTED,
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            actor_user_id=11,
            resource_id=44,
            request_id="t4-audit-reason",
            scope_type=ScopeType.PROPERTY,
            tenant_id=1,
            property_id=2,
            details={
                "house_id": 9,
                "relation_type": "OWNER",
                "review_reason": "must not be audited",
            },
        )
    with pytest.raises(AppError) as scope_error:
        service.append(
            action=AuditAction.RESIDENT_BIND_REQUEST_APPROVED,
            resource_type=AuditResourceType.RESIDENT_BIND_REQUEST,
            actor_user_id=11,
            resource_id=44,
            request_id="t4-audit-system",
            scope_type=ScopeType.SYSTEM,
            details={
                "house_id": 9,
                "relation_type": "OWNER",
                "relation_id": 12,
            },
        )
    assert reason_error.value.code == AuditErrorCode.INVALID_DETAILS
    assert scope_error.value.code == AuditErrorCode.INVALID_SCOPE
    assert repository.append.call_count == 1


def test_employee_attendance_audit_allowlist_and_scope() -> None:
    service, _, repository = _service()

    created = service.append(
        action=AuditAction.ATTENDANCE_CHECKED_IN,
        resource_type=AuditResourceType.ATTENDANCE_RECORD,
        actor_user_id=11,
        resource_id=44,
        request_id="t5-audit-checkin",
        scope_type=ScopeType.PROPERTY,
        tenant_id=1,
        property_id=2,
        details={
            "employee_id": 9,
            "property_id": 2,
            "business_date": "2026-09-17",
        },
    )
    assert created.details_json == {
        "employee_id": 9,
        "property_id": 2,
        "business_date": "2026-09-17",
    }

    with pytest.raises(AppError) as phone_error:
        service.append(
            action=AuditAction.EMPLOYEE_CREATED,
            resource_type=AuditResourceType.EMPLOYEE,
            actor_user_id=11,
            resource_id=44,
            request_id="t5-audit-phone",
            scope_type=ScopeType.TENANT,
            tenant_id=1,
            details={
                "employee_no": "E1",
                "user_id": 9,
                "phone": "13800000000",
            },
        )
    with pytest.raises(AppError) as scope_error:
        service.append(
            action=AuditAction.ATTENDANCE_CHECKED_OUT,
            resource_type=AuditResourceType.ATTENDANCE_RECORD,
            actor_user_id=11,
            resource_id=44,
            request_id="t5-audit-system",
            scope_type=ScopeType.SYSTEM,
            details={
                "employee_id": 9,
                "property_id": 2,
                "business_date": "2026-09-17",
            },
        )
    assert phone_error.value.code == AuditErrorCode.INVALID_DETAILS
    assert scope_error.value.code == AuditErrorCode.INVALID_SCOPE
    assert repository.append.call_count == 1
