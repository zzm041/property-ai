"""Atomic first-class tenant/property onboarding for a system administrator."""

from dataclasses import dataclass
import hashlib
import json
from typing import Callable

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode, IamErrorCode
from app.core.time import validate_timezone
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.model.property import Property, Tenant
from app.model.work_order import IdempotencyRecord
from app.repository.credential import CredentialRepository
from app.repository.identity import IdentityRepository
from app.repository.property import PropertyRepository
from app.service.audit import AuditService
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService
from app.service.identity.rbac_mutation import RbacMutationService

ROLE_MANAGE_PERMISSION = "role:manage"


@dataclass(frozen=True, slots=True)
class OrganizationOnboardingResult:
    tenant: Tenant
    property_record: Property
    initial_admin_user_id: int


def _required_text(value: str, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise AppError(
            code="RES.INVALID_INPUT",
            message=f"{field} is invalid",
            status_code=422,
            details={"field": field},
        )
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise AppError(
            code="RES.INVALID_INPUT",
            message=f"{field} is invalid",
            status_code=422,
            details={"field": field},
        )
    return normalized


class OrganizationOnboardingService:
    """Create one organization and its initial administrator in one transaction."""

    def __init__(
        self,
        session: Session,
        *,
        property_repository: PropertyRepository | None = None,
        credential_repository: CredentialRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        rbac_mutation: RbacMutationService | None = None,
    ) -> None:
        self._session = session
        self._properties = property_repository or PropertyRepository(session)
        self._credentials = credential_repository or CredentialRepository(session)
        self._audit = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session, IdentityRepository(session)
        ).load_principal
        self._rbac = rbac_mutation or RbacMutationService(session)

    def create_organization(
        self,
        *,
        actor: Principal,
        tenant_name: str,
        tenant_contact: str | None,
        property_name: str,
        property_address: str,
        timezone: str,
        initial_admin_phone: str,
        idempotency_key: str,
        request_id: str,
    ) -> OrganizationOnboardingResult:
        from app.core.database import run_owned_transaction

        def operation() -> OrganizationOnboardingResult:
            current = self._require_system_admin(actor.user_id)
            self._validate_idempotency_key(idempotency_key)
            try:
                timezone_name = validate_timezone(timezone).key
            except ValueError as exc:
                raise AppError(
                    code="RES.INVALID_INPUT",
                    message="timezone must be a valid IANA timezone",
                    status_code=422,
                    details={"field": "timezone"},
                ) from exc

            normalized_tenant_name = _required_text(
                tenant_name, "tenant_name", 200
            )
            normalized_contact = (
                _required_text(tenant_contact, "tenant_contact", 255)
                if tenant_contact is not None
                else None
            )
            normalized_property_name = _required_text(
                property_name, "property_name", 200
            )
            normalized_property_address = _required_text(
                property_address, "property_address", 500
            )
            request_hash = self._request_hash(
                tenant_name=normalized_tenant_name,
                tenant_contact=normalized_contact,
                property_name=normalized_property_name,
                property_address=normalized_property_address,
                timezone=timezone_name,
                initial_admin_phone=initial_admin_phone,
            )
            existing = self._properties.get_idempotency(
                user_id=current.user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._load_idempotent_result(
                    existing=existing,
                    expected_hash=request_hash,
                )

            admin_phone = self._credentials.get_active_phone_for_update(
                initial_admin_phone
            )
            if admin_phone is None:
                raise AppError(
                    code=IamErrorCode.USER_NOT_FOUND,
                    message="Initial administrator must first register an active account",
                    status_code=404,
                )

            tenant = self._properties.add_tenant(
                Tenant(
                    name=normalized_tenant_name,
                    status="ACTIVE",
                    contact=normalized_contact,
                )
            )
            property_record = self._properties.add_property(
                Property(
                    tenant_id=tenant.id,
                    name=normalized_property_name,
                    address=normalized_property_address,
                    timezone=timezone_name,
                )
            )
            tenant_scope = OrganizationScope(
                ScopeType.TENANT, tenant_id=tenant.id
            )
            self._rbac._grant_user_role_in_current_transaction(
                actor_user_id=current.user_id,
                target_user_id=admin_phone.user_id,
                role_code="TENANT_ADMIN",
                scope=tenant_scope,
                request_id=request_id,
            )
            self._audit.append(
                action=AuditAction.ORGANIZATION_TENANT_CREATED,
                resource_type=AuditResourceType.TENANT,
                actor_user_id=current.user_id,
                resource_id=tenant.id,
                request_id=request_id,
                scope_type=ScopeType.TENANT,
                tenant_id=tenant.id,
                details={
                    "name": tenant.name,
                    "initial_admin_user_id": admin_phone.user_id,
                },
            )
            self._audit.append(
                action=AuditAction.ORGANIZATION_PROPERTY_CREATED,
                resource_type=AuditResourceType.PROPERTY,
                actor_user_id=current.user_id,
                resource_id=property_record.id,
                request_id=request_id,
                scope_type=ScopeType.PROPERTY,
                tenant_id=tenant.id,
                property_id=property_record.id,
                details={
                    "name": property_record.name,
                    "timezone": property_record.timezone,
                    "initial_admin_user_id": admin_phone.user_id,
                },
            )
            self._properties.add_idempotency(
                IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    user_id=current.user_id,
                    tenant_id=tenant.id,
                    property_id=property_record.id,
                    resource_type="ORGANIZATION_ONBOARDING",
                    resource_id=admin_phone.user_id,
                    request_hash=request_hash,
                )
            )
            return OrganizationOnboardingResult(
                tenant=tenant,
                property_record=property_record,
                initial_admin_user_id=admin_phone.user_id,
            )

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code="RES.DUPLICATE_RESOURCE",
                message="An organization with the same business key already exists",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Organization onboarding could not be completed",
                status_code=500,
            ) from exc

    def _require_system_admin(self, actor_user_id: int) -> Principal:
        try:
            current = self._principal_loader(actor_user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        system_scope = OrganizationScope(ScopeType.SYSTEM)
        is_system_admin = any(
            grant.role_code == "SYSTEM_ADMIN"
            and grant.scope == system_scope
            for grant in current.grants
        )
        if current.status != "ACTIVE" or not is_system_admin:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            current, ROLE_MANAGE_PERMISSION, system_scope
        )
        return current

    def _load_idempotent_result(
        self,
        *,
        existing: IdempotencyRecord,
        expected_hash: str,
    ) -> OrganizationOnboardingResult:
        if existing.request_hash != expected_hash:
            raise AppError(
                code="RES.IDEMPOTENCY_CONFLICT",
                message="Idempotency key was reused with a different request",
                status_code=409,
            )
        if (
            existing.resource_type != "ORGANIZATION_ONBOARDING"
            or existing.tenant_id is None
            or existing.property_id is None
            or existing.resource_id is None
        ):
            raise AppError(
                code="RES.IDEMPOTENCY_CONFLICT",
                message="Idempotent organization result is invalid",
                status_code=409,
            )
        tenant = self._properties.get_tenant(existing.tenant_id)
        property_record = self._properties.get_property(
            tenant_id=existing.tenant_id,
            property_id=existing.property_id,
        )
        if tenant is None or property_record is None:
            raise AppError(
                code="RES.IDEMPOTENCY_CONFLICT",
                message="Idempotent organization result could not be loaded",
                status_code=409,
            )
        return OrganizationOnboardingResult(
            tenant=tenant,
            property_record=property_record,
            initial_admin_user_id=existing.resource_id,
        )

    @staticmethod
    def _request_hash(**values: object) -> str:
        payload = json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise AppError(
                code="RES.IDEMPOTENCY_KEY_REQUIRED",
                message="Idempotency-Key is required",
                status_code=422,
            )
