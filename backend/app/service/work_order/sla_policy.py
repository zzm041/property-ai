"""Scoped, audited SLA policy administration on the existing 0009 schema."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import run_owned_transaction
from app.core.errors import AppError, AuthErrorCode, ErrorCode, IamErrorCode, SlaErrorCode
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.domain.work_order import (
    DEFAULT_SLA_COMPLETE_MINUTES,
    DEFAULT_SLA_RESPOND_MINUTES,
    SLA_POLICY_MANAGE,
    SlaPolicyStatus,
    WorkOrderCategory,
    WorkOrderPriority,
    sla_warning_lead_minutes,
)
from app.model.work_order import SlaPolicy, SlaPolicyVersion
from app.repository.identity import IdentityRepository
from app.repository.property import PropertyRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService
from app.core.time import utc_now


@dataclass(frozen=True, slots=True)
class EffectiveSlaPolicy:
    category: str
    priority: str
    source: str
    policy_id: int | None
    respond_minutes: int
    complete_minutes: int


@dataclass(frozen=True, slots=True)
class SlaPolicyListing:
    property_id: int
    items: tuple[SlaPolicy, ...]
    effective: tuple[EffectiveSlaPolicy, ...]
    versioning_available: bool = False


class SlaPolicyService:
    def __init__(
        self,
        session: Session,
        *,
        repository: WorkOrderRepository | None = None,
        property_repository: PropertyRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or WorkOrderRepository(session)
        self._properties = property_repository or PropertyRepository(session)
        self._audit = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal

    def list_for_property(
        self,
        *,
        actor: Principal,
        property_id: int,
        claimed_tenant_id: int | None = None,
    ) -> SlaPolicyListing:
        property_record = self._properties.get_property_by_id(property_id)
        if property_record is None:
            self._raise_forbidden()
        if claimed_tenant_id is not None and claimed_tenant_id != property_record.tenant_id:
            self._raise_forbidden()
        tenant = self._properties.get_tenant(property_record.tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            self._raise_forbidden()
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(actor, SLA_POLICY_MANAGE, scope)
        records = self._repository.list_sla_policies_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        available = self._versioning_available()
        if available:
            for record in records:
                record._latest_version = self._latest_version(record.id)
        return SlaPolicyListing(
            property_id=property_record.id,
            items=records,
            effective=self._effective(records, property_id=property_record.id),
            versioning_available=available,
        )

    def put_policy(
        self,
        *,
        actor: Principal,
        tenant_id: int,
        property_id: int | None,
        category: str,
        priority: str,
        respond_minutes: int,
        complete_minutes: int,
        accept_minutes: int | None = None,
        warning_lead_minutes: int | None = None,
        escalation_minutes: int | None = None,
        change_reason: str | None = None,
        status: str,
        expected_updated_at: datetime | None,
        request_id: str,
    ) -> SlaPolicy:
        def operation() -> SlaPolicy:
            current = self._reload_actor(actor)
            scope = self._locked_scope(
                tenant_id=tenant_id,
                property_id=property_id,
            )
            RbacService.require_permission(current, SLA_POLICY_MANAGE, scope)
            parsed_category, parsed_priority, parsed_status = self._validate_values(
                category=category,
                priority=priority,
                status=status,
                respond_minutes=respond_minutes,
                complete_minutes=complete_minutes,
            )
            if property_id is None and parsed_status is SlaPolicyStatus.DISABLED:
                raise AppError(
                    code=SlaErrorCode.INVALID_INPUT,
                    message="Tenant default SLA policy cannot be disabled",
                    status_code=422,
                )
            versioning_available = self._versioning_available()
            accept = respond_minutes if accept_minutes is None else accept_minutes
            warning = (
                sla_warning_lead_minutes(complete_minutes)
                if warning_lead_minutes is None
                else warning_lead_minutes
            )
            escalation = 0 if escalation_minutes is None else escalation_minutes
            if accept > respond_minutes:
                raise AppError(code=SlaErrorCode.INVALID_INPUT, message="SLA acceptance must not exceed response", status_code=422)
            cleaned_reason = None if change_reason is None else change_reason.strip()
            record = self._repository.get_sla_policy_for_update(
                tenant_id=tenant_id,
                property_id=property_id,
                category=parsed_category.value,
                priority=parsed_priority.value,
            )
            requested = {
                "respond_minutes": respond_minutes,
                "complete_minutes": complete_minutes,
                "status": parsed_status.value,
            }
            if record is not None:
                before = self._snapshot(record)
                latest = self._latest_version(record.id) if versioning_available else None
                same_version_values = latest is not None and (
                    latest.accept_minutes == accept
                    and latest.respond_minutes == respond_minutes
                    and latest.complete_minutes == complete_minutes
                    and latest.warning_lead_minutes == warning
                    and latest.escalation_minutes == escalation
                    and latest.status == parsed_status.value
                )
                if all(before[key] == value for key, value in requested.items()) and (
                    not versioning_available or same_version_values
                ):
                    record._latest_version = latest
                    record._versioning_available = versioning_available
                    return record
                if versioning_available and not cleaned_reason:
                    raise AppError(code=SlaErrorCode.INVALID_INPUT, message="SLA policy change reason is required", status_code=422)
                if expected_updated_at is None or record.updated_at != expected_updated_at:
                    raise AppError(
                        code=SlaErrorCode.CONFLICT,
                        message="SLA policy changed; refresh before saving",
                        status_code=409,
                        details={"retryable": True},
                    )
                record.respond_minutes = respond_minutes
                record.complete_minutes = complete_minutes
                record.status = parsed_status.value
                self._repository.flush_sla_policy(record)
            else:
                before = None
                if versioning_available and not cleaned_reason:
                    raise AppError(code=SlaErrorCode.INVALID_INPUT, message="SLA policy change reason is required", status_code=422)
                record = self._repository.add_sla_policy(
                    SlaPolicy(
                        tenant_id=tenant_id,
                        property_id=property_id,
                        category=parsed_category.value,
                        priority=parsed_priority.value,
                        respond_minutes=respond_minutes,
                        complete_minutes=complete_minutes,
                        status=parsed_status.value,
                    )
                )
            if versioning_available:
                now = utc_now()
                # The legacy row remains the concurrency token for both old and
                # newly versioned clients. A change that only touches the new
                # accept/warning/escalation fields must still advance it.
                record.updated_at = now
                previous = self._latest_version(record.id)
                if previous is not None and previous.effective_to is None:
                    previous.effective_to = now
                version_no = int(self._session.scalar(select(func.max(SlaPolicyVersion.version_no)).where(SlaPolicyVersion.policy_id == record.id)) or 0) + 1
                version = SlaPolicyVersion(
                    policy_id=record.id,
                    version_no=version_no,
                    accept_minutes=accept,
                    respond_minutes=respond_minutes,
                    complete_minutes=complete_minutes,
                    warning_lead_minutes=warning,
                    escalation_minutes=escalation,
                    status=parsed_status.value,
                    change_reason=cleaned_reason,
                    created_by_user_id=current.user_id,
                    effective_from=now,
                    effective_to=None,
                )
                self._session.add(version)
                self._session.flush()
                record._latest_version = version
            else:
                record._latest_version = None
            record._versioning_available = versioning_available
            self._audit.append(
                action=AuditAction.SLA_POLICY_CHANGED,
                resource_type=AuditResourceType.SLA_POLICY,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                scope_type=scope.scope_type,
                tenant_id=scope.tenant_id,
                property_id=scope.property_id,
                details={
                    "scope_type": scope.scope_type.value,
                    "property_id": property_id,
                    "category": record.category,
                    "priority": record.priority,
                    "before": before,
                    "after": self._snapshot(record),
                },
            )
            return record

        try:
            return run_owned_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=SlaErrorCode.CONFLICT,
                message="SLA policy conflicted with current data",
                status_code=409,
                details={"retryable": True},
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="SLA policy could not be saved",
                status_code=500,
            ) from exc

    def _locked_scope(
        self,
        *,
        tenant_id: int,
        property_id: int | None,
    ) -> OrganizationScope:
        if property_id is None:
            tenant = self._properties.get_tenant_for_update(tenant_id)
            if tenant is None or tenant.status != "ACTIVE":
                self._raise_forbidden()
            return OrganizationScope(ScopeType.TENANT, tenant_id=tenant.id)
        property_record = self._properties.get_property_by_id_for_update(property_id)
        if property_record is None or property_record.tenant_id != tenant_id:
            self._raise_forbidden()
        tenant = self._properties.get_tenant(property_record.tenant_id)
        if tenant is None or tenant.status != "ACTIVE":
            self._raise_forbidden()
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )

    def _reload_actor(self, actor: Principal) -> Principal:
        if actor.status != "ACTIVE":
            raise AppError(
                code=AuthErrorCode.USER_DISABLED,
                message="User is not active",
                status_code=401,
            )
        try:
            current = self._principal_loader(actor.user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if current.user_id != actor.user_id or current.status != "ACTIVE":
            self._raise_forbidden()
        return current

    @staticmethod
    def _validate_values(
        *,
        category: str,
        priority: str,
        status: str,
        respond_minutes: int,
        complete_minutes: int,
    ) -> tuple[WorkOrderCategory, WorkOrderPriority, SlaPolicyStatus]:
        if (
            isinstance(respond_minutes, bool)
            or isinstance(complete_minutes, bool)
            or respond_minutes < 1
            or complete_minutes < 1
            or respond_minutes > complete_minutes
        ):
            raise AppError(
                code=SlaErrorCode.INVALID_INPUT,
                message=(
                    "SLA minutes must be positive and the response window "
                    "must not exceed the completion window"
                ),
                status_code=422,
            )
        try:
            return (
                WorkOrderCategory(category),
                WorkOrderPriority(priority),
                SlaPolicyStatus(status),
            )
        except (TypeError, ValueError) as exc:
            raise AppError(
                code=SlaErrorCode.INVALID_INPUT,
                message="SLA category, priority, or status is invalid",
                status_code=422,
            ) from exc

    @staticmethod
    def _snapshot(record: SlaPolicy) -> dict[str, object]:
        return {
            "respond_minutes": int(record.respond_minutes),
            "complete_minutes": int(record.complete_minutes),
            "status": str(record.status),
        }

    def _versioning_available(self) -> bool:
        try:
            inspector = inspect(self._session.get_bind())
            return (
                inspector.has_table("sla_policy_versions") is True
                and inspector.has_table("work_order_sla_applications") is True
            )
        except (AttributeError, SQLAlchemyError):
            return False

    def _latest_version(self, policy_id: int) -> SlaPolicyVersion | None:
        return self._session.scalar(
            select(SlaPolicyVersion)
            .where(SlaPolicyVersion.policy_id == policy_id)
            .order_by(SlaPolicyVersion.version_no.desc(), SlaPolicyVersion.id.desc())
            .limit(1)
        )

    @staticmethod
    def _effective(
        records: tuple[SlaPolicy, ...],
        *,
        property_id: int,
    ) -> tuple[EffectiveSlaPolicy, ...]:
        active = {
            (record.property_id, record.category, record.priority): record
            for record in records
            if record.status == SlaPolicyStatus.ACTIVE.value
        }
        resolved: list[EffectiveSlaPolicy] = []
        for category in WorkOrderCategory:
            for priority in WorkOrderPriority:
                record = active.get((property_id, category.value, priority.value))
                source = "PROPERTY"
                if record is None:
                    record = active.get((None, category.value, priority.value))
                    source = "TENANT"
                if record is None:
                    source = "CODE_DEFAULT"
                    policy_id = None
                    respond_minutes = DEFAULT_SLA_RESPOND_MINUTES[priority]
                    complete_minutes = DEFAULT_SLA_COMPLETE_MINUTES[priority]
                else:
                    policy_id = record.id
                    respond_minutes = record.respond_minutes
                    complete_minutes = record.complete_minutes
                resolved.append(
                    EffectiveSlaPolicy(
                        category=category.value,
                        priority=priority.value,
                        source=source,
                        policy_id=policy_id,
                        respond_minutes=respond_minutes,
                        complete_minutes=complete_minutes,
                    )
                )
        return tuple(resolved)

    @staticmethod
    def _raise_forbidden() -> None:
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Permission denied",
            status_code=403,
        )
