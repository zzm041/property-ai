"""WorkOrder create, FSM, manual dispatch, processing, and receipt use cases."""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    AuthErrorCode,
    ErrorCode,
    FileErrorCode,
    IamErrorCode,
    WorkOrderErrorCode,
)
from app.core.logging import get_logger
from app.core.time import current_business_date, utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.employee import AttendanceOnDuty, EmployeeStatus
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.work_order import (
    WORK_ORDER_CANCEL,
    WORK_ORDER_CREATE,
    WORK_ORDER_DECIDE,
    WORK_ORDER_DISPATCH,
    WORK_ORDER_PROCESS,
    WORK_ORDER_READ,
    WORK_ORDER_RESOLVE,
    AssignmentStatus,
    MediaKind,
    MediaTakenPhase,
    PENDING_DISPATCH_STATUSES,
    ProcessReason,
    SlaClockState,
    SupervisorDecision,
    WAITING_PROCESS_REASONS,
    WorkOrderActorType,
    WorkOrderCategory,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderSeverity,
    WorkOrderStatus,
    compute_sla_due_at,
    employee_is_capacity_available,
    require_legal_transition,
    sla_is_overdue,
    sla_windows_for_priority,
    staff_may_submit_resolved_receipt,
)
from app.model.employee import Employee
from app.model.work_order import (
    FileObject,
    IdempotencyRecord,
    SlaClock,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderContent,
    WorkOrderFeedback,
    WorkOrderLog,
    WorkOrderMedia,
    WorkOrderProcess,
    WorkOrderReceipt,
)
from app.provider.factory import build_storage_provider
from app.provider.storage import StorageProvider
from app.repository.employee import EmployeeRepository
from app.repository.identity import IdentityRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

T = TypeVar("T")
logger = get_logger(__name__)
_ALLOWED_MIME = {
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "audio/mpeg": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO,
    "audio/wav": MediaKind.AUDIO,
    "audio/webm": MediaKind.AUDIO,
}
_ACTIVE_ASSIGNMENT_UNIQUE = frozenset(
    {"uq_work_order_assignments_active_work_order"}
)
_IDEMPOTENCY_UNIQUE = frozenset({"uq_idempotency_records_key"})
_HOUSE_SCOPE_FK = frozenset({"fk_work_orders_house_property_tenant_houses"})
_QUEUE_STATUSES = tuple(
    status.value for status in PENDING_DISPATCH_STATUSES
) + (
    WorkOrderStatus.PROCESSING.value,
)
_ESCALATION_REASONS = frozenset(
    {
        ProcessReason.ESCALATED.value,
        ProcessReason.INTERNAL_UNRESOLVED.value,
        ProcessReason.REASSIGN_REQUIRED.value,
    }
)


class WorkOrderService:
    """Own WorkOrder transactions; repositories never commit."""

    def __init__(
        self,
        session: Session,
        repository: WorkOrderRepository | None = None,
        employee_repository: EmployeeRepository | None = None,
        resident_repository: ResidentBindingRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        storage: StorageProvider | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._repository = repository or WorkOrderRepository(session)
        self._employees = employee_repository or EmployeeRepository(session)
        self._residents = resident_repository or ResidentBindingRepository(session)
        self._audit_service = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal
        self._settings = settings or get_settings()
        self._storage = storage or build_storage_provider(self._settings)
        self._clock = clock

    def register_file(
        self,
        *,
        actor: Principal,
        mime_type: str,
        size_bytes: int,
        request_id: str,
        original_name: str | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> FileObject:
        self._require_active_actor(actor)
        self._validate_request_id(request_id)
        kind = _ALLOWED_MIME.get(mime_type.strip().casefold())
        if kind is None:
            raise AppError(
                code=FileErrorCode.UNSUPPORTED_TYPE,
                message="File type is not allowed",
                status_code=422,
            )
        max_bytes = self._settings.upload_max_mb * 1024 * 1024
        if size_bytes < 0 or size_bytes > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        property_id = claimed_property_id
        tenant_id = claimed_tenant_id
        stored_holder: dict[str, object] = {}

        def _persist() -> FileObject:
            resolved_property_id = property_id
            resolved_tenant_id = tenant_id
            if resolved_property_id is not None:
                property_record = (
                    self._employees.get_property_in_tenant(
                        tenant_id=resolved_tenant_id or 0,
                        property_id=resolved_property_id,
                    )
                    if resolved_tenant_id
                    else self._repository.get_property(resolved_property_id)
                )
                if property_record is None:
                    raise AppError(
                        code=FileErrorCode.INVALID_INPUT,
                        message="Target property was not found",
                        status_code=422,
                    )
                resolved_tenant_id = property_record.tenant_id
            elif resolved_tenant_id is None:
                scoped = next(
                    (
                        grant.scope
                        for grant in actor.grants
                        if grant.scope.tenant_id is not None
                    ),
                    None,
                )
                if scoped is None or scoped.tenant_id is None:
                    raise AppError(
                        code=FileErrorCode.INVALID_INPUT,
                        message="A tenant context is required to register a file",
                        status_code=422,
                    )
                resolved_tenant_id = scoped.tenant_id
                resolved_property_id = scoped.property_id
            stored = self._storage.register_object(
                mime_type=mime_type,
                size_bytes=size_bytes,
                filename=original_name,
                tenant_id=resolved_tenant_id,
                property_id=resolved_property_id,
            )
            stored_holder["stored"] = stored
            digest = sha256(
                f"{stored.storage_key}:{mime_type}:{size_bytes}".encode()
            ).hexdigest()
            record = self._repository.add_file(
                FileObject(
                    tenant_id=resolved_tenant_id,
                    property_id=resolved_property_id,
                    uploader_user_id=actor.user_id,
                    content_hash=digest,
                    mime_type=mime_type.strip().casefold(),
                    size_bytes=size_bytes,
                    storage_key=stored.storage_key,
                    original_name=original_name,
                    scanner_status=stored.scanner_status,
                )
            )
            return record

        record = self._execute(_persist)
        stored = stored_holder.get("stored")
        if stored is not None:
            record.upload_url = stored.upload_url  # type: ignore[attr-defined]
            record.upload_expires_in = stored.upload_expires_in  # type: ignore[attr-defined]
        return record

    def put_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        content: bytes,
        request_id: str,
    ) -> FileObject:
        self._require_active_actor(actor)
        self._validate_request_id(request_id)
        self._require_positive_id(file_id, "file_id")
        record = self._repository.get_file(file_id)
        if record is None or not self._actor_can_see_file_tenant(actor, record.tenant_id):
            raise AppError(
                code=FileErrorCode.NOT_FOUND,
                message="File was not found",
                status_code=404,
            )
        if record.uploader_user_id != actor.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        max_bytes = self._settings.upload_max_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError

        try:
            self._storage.put_object(record.storage_key, content, record.mime_type)
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        return record

    def list_my_work_orders(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        current = self._reload_actor(actor)
        return self._repository.list_for_reporter(
            user_id=current.user_id,
            after_id=after_id,
            limit=self._page_limit(limit),
        )

    def create_work_order(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        idempotency_key: str,
        text: str | None = None,
        file_ids: tuple[int, ...] = (),
        category: str | None = None,
        priority: str | None = None,
        severity: str | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> WorkOrder:
        self._require_positive_id(house_id, "house_id")
        self._validate_request_id(request_id)
        self._validate_idempotency_key(idempotency_key)
        record = self._execute(
            lambda: self._create_work_order(
                actor=actor,
                house_id=house_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
                text=text,
                file_ids=file_ids,
                category=category,
                priority=priority,
                severity=severity,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            )
        )
        self._enqueue_analysis(record.id, request_id)
        return record

    def _enqueue_analysis(self, work_order_id: int, request_id: str) -> None:
        if not self._settings.should_enqueue_ai_on_create:
            return
        from app.worker.enqueue import enqueue_celery_task
        from app.worker.tasks import analyze_work_order_task

        enqueue_celery_task(
            settings=self._settings,
            celery_task=analyze_work_order_task,
            args=(work_order_id, request_id),
            event="ai_enqueue_failed",
            extra={"work_order_id": work_order_id},
        )

    def get_work_order(self, *, actor: Principal, work_order_id: int) -> WorkOrder:
        self._require_positive_id(work_order_id, "work_order_id")
        current = self._reload_actor(actor)
        record = self._repository.get_work_order(work_order_id)
        if record is None:
            self._raise_not_found()
        self._authorize_read(current, record)
        return record

    def get_work_order_detail(
        self,
        *,
        actor: Principal,
        work_order_id: int,
    ) -> tuple[
        WorkOrder,
        WorkOrderContent | None,
        tuple[WorkOrderMedia, ...],
        tuple[WorkOrderAssignment, ...],
        tuple[WorkOrderLog, ...],
    ]:
        record = self.get_work_order(actor=actor, work_order_id=work_order_id)
        return (
            record,
            self._repository.get_content(record.id),
            self._repository.list_media(record.id),
            self._repository.list_assignments(record.id),
            self._repository.list_logs(record.id),
        )

    def list_supervisor_queue(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        claimed_tenant_id: int | None = None,
    ) -> tuple[WorkOrder, ...]:
        self._require_positive_id(property_id, "property_id")
        current = self._reload_actor(actor)
        property_record = self._repository.get_property(property_id)
        if property_record is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_FOUND,
                message="Work order was not found",
                status_code=404,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != property_record.tenant_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared tenant does not match the target property",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(current, WORK_ORDER_DISPATCH, scope)
        records = self._repository.list_queue_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            statuses=_QUEUE_STATUSES,
            after_id=after_id,
            limit=self._page_limit(limit),
        )
        return tuple(
            item
            for item in records
            if item.status in {status.value for status in PENDING_DISPATCH_STATUSES}
            or item.process_reason in _ESCALATION_REASONS
        )

    def list_staff_work_orders(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        current = self._reload_actor(actor)
        employee = self._require_actor_employee(current)
        return self._repository.list_for_assignee(
            employee_id=employee.id,
            after_id=after_id,
            limit=self._page_limit(limit),
        )

    def append_content(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None = None,
        file_ids: tuple[int, ...] = (),
        taken_phase: str | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._append_content(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                text=text,
                file_ids=file_ids,
                taken_phase=taken_phase,
            )
        )

    def cancel(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._cancel(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason,
                version=version,
            )
        )

    def confirm(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._confirm(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )

    def reopen(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not isinstance(reason, str) or not reason.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
            )
        return self._execute(
            lambda: self._reopen(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            )
        )

    def rate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        rating: int,
        comment: str | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if rating < 1 or rating > 5:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="rating is invalid",
                status_code=422,
            )
        return self._execute(
            lambda: self._rate(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                rating=rating,
                comment=comment,
            )
        )

    def assign(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        employee_id: int,
        request_id: str,
        reason: str | None = None,
        version: int | None = None,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> WorkOrder:
        self._require_positive_id(employee_id, "employee_id")
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._assign(
                actor=actor,
                work_order_id=work_order_id,
                employee_id=employee_id,
                request_id=request_id,
                reason=reason,
                version=version,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            )
        )

    def decide(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        decision: str,
        request_id: str,
        employee_id: int | None = None,
        reason: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._decide(
                actor=actor,
                work_order_id=work_order_id,
                decision=decision,
                request_id=request_id,
                employee_id=employee_id,
                reason=reason,
                version=version,
            )
        )

    def accept(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._accept(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )

    def reject(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not isinstance(reason, str) or not reason.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
            )
        return self._execute(
            lambda: self._reject(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            )
        )

    def start(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._start(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )

    def feedback(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None = None,
        process_reason: str | None = None,
        file_ids: tuple[int, ...] = (),
        taken_phase: str | None = None,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        return self._execute(
            lambda: self._feedback(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                text=text,
                process_reason=process_reason,
                file_ids=file_ids,
                taken_phase=taken_phase,
                version=version,
            )
        )

    def submit_receipt(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        problem_type: str,
        method: str,
        resolved: bool,
        materials: str | None = None,
        fee_amount: Decimal | None = None,
        fee_flag: str = "NONE",
        unresolved_reason: str | None = None,
        follow_up: str | None = None,
        file_ids: tuple[int, ...] = (),
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not problem_type.strip() or not method.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt required fields are missing",
                status_code=422,
            )
        return self._execute(
            lambda: self._submit_receipt(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                problem_type=problem_type.strip(),
                method=method.strip(),
                resolved=resolved,
                materials=materials,
                fee_amount=fee_amount,
                fee_flag=fee_flag,
                unresolved_reason=unresolved_reason,
                follow_up=follow_up,
                file_ids=file_ids,
                version=version,
            )
        )

    def escalate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        process_reason: str,
        reason: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        if not reason.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason is required",
                status_code=422,
            )
        return self._execute(
            lambda: self._escalate(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                process_reason=process_reason,
                reason=reason.strip(),
                version=version,
            )
        )

    def is_overdue(self, record: WorkOrder, now: datetime | None = None) -> bool:
        return sla_is_overdue(due_at=record.sla_due_at, now=now or self._clock())

    def _create_work_order(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        idempotency_key: str,
        text: str | None,
        file_ids: tuple[int, ...],
        category: str | None,
        priority: str | None,
        severity: str | None,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        cleaned_text = self._optional_text(text)
        if cleaned_text is None and not file_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Work order content requires text or media",
                status_code=422,
            )
        request_hash = self._request_hash(
            house_id=house_id,
            text=cleaned_text,
            file_ids=file_ids,
            category=category,
            priority=priority,
            severity=severity,
        )
        existing = self._repository.get_idempotency(
            user_id=current.user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    code=WorkOrderErrorCode.IDEMPOTENCY_CONFLICT,
                    message="Idempotency key was reused with a different request",
                    status_code=409,
                )
            record = self._repository.get_work_order(existing.resource_id)
            if record is None:
                raise AppError(
                    code=WorkOrderErrorCode.CONFLICT,
                    message="Idempotent work order could not be loaded",
                    status_code=409,
                )
            return record
        house = self._residents.find_active_relation(
            user_id=current.user_id,
            house_id=house_id,
            now=self._clock(),
        )
        owned = self._residents.get_active_house_for_user(
            user_id=current.user_id,
            house_id=house_id,
            now=self._clock(),
        )
        if house is None or owned is None:
            registered = self._repository.get_house(house_id)
            if registered is None:
                raise AppError(
                    code=WorkOrderErrorCode.NOT_FOUND,
                    message="Work order was not found",
                    status_code=404,
                )
            raise AppError(
                code=WorkOrderErrorCode.HOUSE_NOT_OWNED,
                message="Caller does not have an effective house relation",
                status_code=403,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != owned.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != owned.property_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared scope does not match the target house",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=owned.tenant_id,
            property_id=owned.property_id,
        )
        RbacService.require_permission(current, WORK_ORDER_CREATE, scope)
        category_value = self._parse_enum(
            category, WorkOrderCategory, WorkOrderCategory.OTHER, "category"
        )
        priority_value = self._parse_enum(
            priority, WorkOrderPriority, WorkOrderPriority.MEDIUM, "priority"
        )
        severity_value = self._parse_enum(
            severity, WorkOrderSeverity, WorkOrderSeverity.LOW, "severity"
        )
        now = self._clock()
        policy = self._repository.find_sla_policy(
            tenant_id=owned.tenant_id,
            property_id=owned.property_id,
            category=category_value.value,
            priority=priority_value.value,
        )
        respond_minutes, complete_minutes = sla_windows_for_priority(priority_value)
        if policy is not None:
            respond_minutes = policy.respond_minutes
            complete_minutes = policy.complete_minutes
        due_at = compute_sla_due_at(
            created_at=now,
            priority=priority_value,
            complete_minutes=complete_minutes,
        )
        record = self._repository.add_work_order(
            WorkOrder(
                tenant_id=owned.tenant_id,
                property_id=owned.property_id,
                house_id=owned.id,
                reporter_user_id=current.user_id,
                status=WorkOrderStatus.PENDING_ANALYSIS.value,
                process_reason=ProcessReason.NONE.value,
                category=category_value.value,
                severity=severity_value.value,
                priority=priority_value.value,
                sla_policy_id=None if policy is None else policy.id,
                sla_due_at=due_at,
                version=1,
            )
        )
        self._repository.add_content(
            WorkOrderContent(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                text=cleaned_text,
            )
        )
        self._attach_media(
            record,
            actor=current,
            file_ids=file_ids,
            taken_phase=MediaTakenPhase.REPORT,
        )
        warning_delta = max(complete_minutes // 4, 15)
        self._repository.add_sla_clock(
            SlaClock(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                accept_due_at=now + timedelta(minutes=respond_minutes),
                respond_due_at=now + timedelta(minutes=respond_minutes),
                complete_due_at=due_at,
                warning_at=due_at - timedelta(minutes=warning_delta),
                state=SlaClockState.OPEN.value,
            )
        )
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=None,
            to_status=WorkOrderStatus.PENDING_ANALYSIS,
            event=WorkOrderEvent.CREATE,
            request_id=request_id,
        )
        self._repository.add_idempotency(
            IdempotencyRecord(
                idempotency_key=idempotency_key,
                user_id=current.user_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                resource_type="WORK_ORDER",
                resource_id=record.id,
                request_hash=request_hash,
            )
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_CREATED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "priority": record.priority,
                "category": record.category,
            },
        )
        return record

    def _append_content(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None,
        file_ids: tuple[int, ...],
        taken_phase: str | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._authorize_read(current, record)
        if record.reporter_user_id != current.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        if record.status in {
            WorkOrderStatus.COMPLETED.value,
            WorkOrderStatus.CANCELLED.value,
            WorkOrderStatus.CLOSED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order can no longer be updated",
                status_code=409,
            )
        cleaned = self._optional_text(text)
        if cleaned is None and not file_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Append requires text or media",
                status_code=422,
            )
        content = self._repository.get_content(record.id)
        if content is not None and cleaned is not None:
            notes = content.extra_notes or ""
            content.extra_notes = f"{notes}\n{cleaned}".strip() if notes else cleaned
        phase = MediaTakenPhase.REPORT
        if taken_phase is not None:
            phase = self._parse_enum(
                taken_phase,
                MediaTakenPhase,
                MediaTakenPhase.REPORT,
                "taken_phase",
            )
        self._attach_media(record, actor=current, file_ids=file_ids, taken_phase=phase)
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=WorkOrderStatus(record.status),
            to_status=WorkOrderStatus(record.status),
            event=WorkOrderEvent.APPEND,
            request_id=request_id,
        )
        self._session.flush()
        return record

    def _cancel(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str | None,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        scope = self._property_scope(record)
        is_reporter = record.reporter_user_id == current.user_id
        if is_reporter:
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
            if record.status != WorkOrderStatus.PENDING_ANALYSIS.value:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Resident may cancel only before assignment",
                    status_code=409,
                )
        else:
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
        self._apply_transition(
            record,
            event=WorkOrderEvent.CANCEL,
            to_status=WorkOrderStatus.CANCELLED,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="CANCELLED",
        )
        self._stop_sla(record)
        self._append_audit(
            action=AuditAction.WORK_ORDER_CANCELLED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={"work_order_id": record.id, "status": record.status},
        )
        return record

    def _confirm(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        self._require_reporter(current, record)
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                confirmed=1,
                unresolved_flag=0,
            )
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.CONFIRM,
            to_status=WorkOrderStatus.COMPLETED,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            request_id=request_id,
            reason_code="CONFIRMED",
        )
        self._end_active_assignment(
            record,
            now=self._clock(),
            status=AssignmentStatus.ENDED,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ARCHIVE,
            to_status=WorkOrderStatus.CLOSED,
            actor=current,
            actor_type=WorkOrderActorType.SYSTEM,
            request_id=request_id,
            reason_code="ARCHIVED",
        )
        record.closed_at = self._clock()
        self._stop_sla(record)
        self._append_audit(
            action=AuditAction.WORK_ORDER_CLOSED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={"work_order_id": record.id, "status": record.status},
        )
        return record

    def _reopen(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        self._require_reporter(current, record)
        assignment = self._repository.get_active_assignment_for_update(record.id)
        employee = None
        if assignment is not None:
            employee = self._employees.get_employee(assignment.employee_id)
        eligible = (
            employee is not None
            and self._eligibility_error(
                record,
                employee,
                ignore_capacity=True,
            )
            is None
        )
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                confirmed=0,
                unresolved_flag=1,
                comment=reason,
            )
        )
        if eligible and employee is not None:
            self._apply_transition(
                record,
                event=WorkOrderEvent.REOPEN,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=WorkOrderActorType.RESIDENT,
                request_id=request_id,
                reason_code="REOPEN",
            )
            next_cycle = 1 if process is None else process.cycle_no + 1
            self._repository.add_process(
                WorkOrderProcess(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    cycle_no=next_cycle,
                    assignee_id=employee.id,
                    started_at=self._clock(),
                )
            )
        else:
            self._end_active_assignment(
                record,
                now=self._clock(),
                status=AssignmentStatus.ENDED,
            )
            self._apply_transition(
                record,
                event=WorkOrderEvent.REOPEN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=WorkOrderActorType.RESIDENT,
                request_id=request_id,
                reason_code="REOPEN",
            )
        self._append_audit(
            action=AuditAction.WORK_ORDER_REOPENED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "reason_code": "REOPEN",
            },
        )
        return record

    def _rate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        rating: int,
        comment: str | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._require_reporter(current, record)
        if record.status not in {
            WorkOrderStatus.COMPLETED.value,
            WorkOrderStatus.CLOSED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Work order is not ready for rating",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        self._repository.add_feedback(
            WorkOrderFeedback(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                process_id=None if process is None else process.id,
                actor_user_id=current.user_id,
                rating=rating,
                comment=self._optional_text(comment),
                unresolved_flag=0,
            )
        )
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=WorkOrderStatus(record.status),
            to_status=WorkOrderStatus(record.status),
            event=WorkOrderEvent.RATE,
            request_id=request_id,
        )
        return record

    def _assign(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        employee_id: int,
        request_id: str,
        reason: str | None,
        version: int | None,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != record.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != record.property_id
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="Declared scope does not match the work order",
                status_code=422,
            )
        RbacService.require_permission(
            current,
            WORK_ORDER_DISPATCH,
            self._property_scope(record),
        )
        employee = self._employees.get_employee_in_tenant(
            tenant_id=record.tenant_id,
            employee_id=employee_id,
        )
        if employee is None:
            raise AppError(
                code=WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE,
                message="Employee is not eligible for dispatch",
                status_code=409,
            )
        now = self._clock()
        current_status = WorkOrderStatus(record.status)
        is_first = current_status in PENDING_DISPATCH_STATUSES
        if is_first and current_status is WorkOrderStatus.PENDING_ANALYSIS:
            self._apply_transition(
                record,
                event=WorkOrderEvent.ASSIGN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="ANALYZE",
            )
        elif not is_first:
            if current_status not in {
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PROCESSING,
            }:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Work order cannot be assigned from the current status",
                    status_code=409,
                )
            if reason is None or not reason.strip():
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                )
            self._end_active_assignment(
                record,
                now=now,
                status=AssignmentStatus.REASSIGNED,
                reason=reason,
            )
            self._apply_transition(
                record,
                event=WorkOrderEvent.REASSIGN,
                to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="REASSIGN",
            )
        else:
            existing = self._repository.get_active_assignment(record.id)
            if existing is not None:
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                )
        eligibility = self._eligibility_error(record, employee)
        if eligibility is not None:
            raise eligibility
        assignment = self._create_assignment(
            record,
            employee=employee,
            actor=current,
            now=now,
            reason=reason,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ASSIGN,
            to_status=WorkOrderStatus.ASSIGNED,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="ASSIGNED",
        )
        record.current_assignment_id = assignment.id
        record.assigned_at = now
        record.accepted_at = None
        record.started_at = None
        record.resolved_at = None
        self._adjust_load(record, employee, delta=1)
        audit_action = (
            AuditAction.WORK_ORDER_ASSIGNED
            if is_first
            else AuditAction.WORK_ORDER_REASSIGNED
        )
        details: dict[str, object] = {
            "work_order_id": record.id,
            "employee_id": employee.id,
            "status": record.status,
        }
        if not is_first:
            details["reason_code"] = "REASSIGN"
        self._append_audit(
            action=audit_action,
            resource_type=AuditResourceType.WORK_ORDER_ASSIGNMENT,
            actor_user_id=current.user_id,
            resource_id=assignment.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details=details,
        )
        from app.service.analysis import AnalysisService

        AnalysisService(self._session, settings=self._settings).mark_assignment_choice(
            work_order=record,
            employee_id=employee.id,
            actor_user_id=current.user_id,
            request_id=request_id,
        )
        self._notify(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            user_id=employee.user_id,
            title="工单已派单",
            body="You have a new assigned work order",
            resource_type="WORK_ORDER",
            resource_id=record.id,
        )
        return record

    def _decide(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        decision: str,
        request_id: str,
        employee_id: int | None,
        reason: str | None,
        version: int | None,
    ) -> WorkOrder:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        RbacService.require_permission(
            current,
            WORK_ORDER_DECIDE,
            self._property_scope(record),
        )
        try:
            code = SupervisorDecision(decision)
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="decision is invalid",
                status_code=422,
            ) from exc
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Decision is only allowed while processing",
                status_code=409,
            )
        if code is SupervisorDecision.REASSIGN:
            if employee_id is None:
                self._end_active_assignment(
                    record,
                    now=self._clock(),
                    status=AssignmentStatus.REASSIGNED,
                    reason=reason,
                )
                self._apply_transition(
                    record,
                    event=WorkOrderEvent.REASSIGN,
                    to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
                    actor=current,
                    actor_type=self._actor_type(current, record),
                    request_id=request_id,
                    reason_code=ProcessReason.REASSIGN_REQUIRED.value,
                )
            else:
                return self._assign(
                    actor=current,
                    work_order_id=record.id,
                    employee_id=employee_id,
                    request_id=request_id,
                    reason=reason or "REASSIGN",
                    version=record.version,
                    claimed_tenant_id=None,
                    claimed_property_id=None,
                )
        else:
            reason_map = {
                SupervisorDecision.WAIT_PARTS: ProcessReason.WAITING_PARTS,
                SupervisorDecision.WAIT_OWNER: ProcessReason.WAITING_OWNER,
                SupervisorDecision.EXTERNAL_COORDINATION: (
                    ProcessReason.EXTERNAL_COORDINATION
                ),
                SupervisorDecision.CONTINUE: ProcessReason(record.process_reason),
            }
            target_reason = reason_map[code]
            self._apply_transition(
                record,
                event=WorkOrderEvent.DECIDE,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                process_reason=target_reason,
                reason_code=code.value,
            )
        self._append_audit(
            action=AuditAction.WORK_ORDER_DECIDED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "process_reason": record.process_reason,
                "reason_code": code.value,
            },
        )
        return record

    def _accept(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        now = self._clock()
        assignment.accept_at = now
        self._apply_transition(
            record,
            event=WorkOrderEvent.ACCEPT,
            to_status=WorkOrderStatus.ACCEPTED,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            request_id=request_id,
            reason_code="ACCEPTED",
        )
        record.accepted_at = now
        self._append_audit(
            action=AuditAction.WORK_ORDER_ACCEPTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": employee.id,
                "status": record.status,
            },
        )
        return record

    def _reject(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        now = self._clock()
        assignment.reject_reason = reason
        self._end_active_assignment(
            record,
            now=now,
            status=AssignmentStatus.REJECTED,
            reason=reason,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.REJECT,
            to_status=WorkOrderStatus.PENDING_ASSIGNMENT,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            request_id=request_id,
            reason_code="REJECTED",
        )
        record.current_assignment_id = None
        self._append_audit(
            action=AuditAction.WORK_ORDER_REJECTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "employee_id": employee.id,
                "status": record.status,
                "reason_code": "REJECTED",
            },
        )
        return record

    def _start(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        now = self._clock()
        self._apply_transition(
            record,
            event=WorkOrderEvent.START,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="STARTED",
        )
        record.started_at = now
        existing = self._repository.get_current_process(record.id)
        cycle_no = 1 if existing is None else existing.cycle_no + 1
        if existing is None or existing.ended_at is not None:
            self._repository.add_process(
                WorkOrderProcess(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    cycle_no=cycle_no if existing is not None else 1,
                    assignee_id=employee.id,
                    started_at=now,
                )
            )
        self._append_audit(
            action=AuditAction.WORK_ORDER_PROCESS_STARTED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={"work_order_id": record.id, "status": record.status},
        )
        return record

    def _feedback(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None,
        process_reason: str | None,
        file_ids: tuple[int, ...],
        taken_phase: str | None,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Feedback is only allowed while processing",
                status_code=409,
            )
        cleaned = self._optional_text(text)
        if cleaned is not None:
            content = self._repository.get_content(record.id)
            if content is not None:
                notes = content.extra_notes or ""
                content.extra_notes = (
                    f"{notes}\n{cleaned}".strip() if notes else cleaned
                )
        phase = MediaTakenPhase.ON_SITE
        if taken_phase is not None:
            phase = self._parse_enum(
                taken_phase,
                MediaTakenPhase,
                MediaTakenPhase.ON_SITE,
                "taken_phase",
            )
        self._attach_media(record, actor=current, file_ids=file_ids, taken_phase=phase)
        target_reason = ProcessReason(record.process_reason)
        if process_reason is not None:
            target_reason = self._parse_enum(
                process_reason,
                ProcessReason,
                ProcessReason.NONE,
                "process_reason",
            )
            if target_reason is ProcessReason.INTERNAL_UNRESOLVED:
                target_reason = ProcessReason.ESCALATED
        self._apply_transition(
            record,
            event=WorkOrderEvent.PROCESS_UPDATE,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            process_reason=target_reason,
            reason_code=target_reason.value,
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_PROCESS_UPDATED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "process_reason": record.process_reason,
            },
        )
        return record

    def _submit_receipt(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        problem_type: str,
        method: str,
        resolved: bool,
        materials: str | None,
        fee_amount: Decimal | None,
        fee_flag: str,
        unresolved_reason: str | None,
        follow_up: str | None,
        file_ids: tuple[int, ...],
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        RbacService.require_permission(
            current,
            WORK_ORDER_RESOLVE,
            self._property_scope(record),
        )
        if record.status != WorkOrderStatus.PROCESSING.value:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order is not in processing",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        if process is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Processing cycle has not started",
                status_code=409,
            )
        if self._repository.get_receipt_for_process(process.id) is not None:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Receipt already exists for this process cycle",
                status_code=409,
            )
        now = self._clock()
        if resolved:
            if not staff_may_submit_resolved_receipt(record.process_reason):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Unresolved process reason cannot be completed",
                    status_code=409,
                )
            self._attach_media(
                record,
                actor=current,
                file_ids=file_ids,
                taken_phase=MediaTakenPhase.AFTER,
            )
            self._repository.add_receipt(
                WorkOrderReceipt(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    process_id=process.id,
                    problem_type=problem_type,
                    method=method,
                    materials=materials,
                    fee_amount=fee_amount,
                    fee_flag=fee_flag or "NONE",
                    resolved=1,
                    follow_up=follow_up,
                    finished_at=now,
                )
            )
            process.ended_at = now
            record.resolved_at = now
            self._apply_transition(
                record,
                event=WorkOrderEvent.RECEIPT_RESOLVED,
                to_status=WorkOrderStatus.WAIT_CONFIRM,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                reason_code="RESOLVED",
            )
            self._append_audit(
                action=AuditAction.WORK_ORDER_RESOLVED,
                resource_type=AuditResourceType.WORK_ORDER,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details={"work_order_id": record.id, "status": record.status},
            )
            self._notify(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=record.reporter_user_id,
                title="工单待确认",
                body="Please confirm the work order result",
                resource_type="WORK_ORDER",
                resource_id=record.id,
            )
        else:
            if unresolved_reason is None or not unresolved_reason.strip():
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="unresolved_reason is required",
                    status_code=422,
                )
            target_reason = ProcessReason.INTERNAL_UNRESOLVED
            self._repository.add_receipt(
                WorkOrderReceipt(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    process_id=process.id,
                    problem_type=problem_type,
                    method=method,
                    materials=materials,
                    fee_amount=fee_amount,
                    fee_flag=fee_flag or "NONE",
                    resolved=0,
                    unresolved_reason=unresolved_reason.strip(),
                    follow_up=follow_up,
                    finished_at=now,
                )
            )
            self._apply_transition(
                record,
                event=WorkOrderEvent.RECEIPT_UNRESOLVED,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                process_reason=target_reason,
                reason_code=target_reason.value,
            )
            self._append_audit(
                action=AuditAction.WORK_ORDER_PROCESS_UPDATED,
                resource_type=AuditResourceType.WORK_ORDER,
                actor_user_id=current.user_id,
                resource_id=record.id,
                request_id=request_id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                details={
                    "work_order_id": record.id,
                    "status": record.status,
                    "process_reason": record.process_reason,
                },
            )
        return record

    def _escalate(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        process_reason: str,
        reason: str,
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=True
        )
        target = self._parse_enum(
            process_reason,
            ProcessReason,
            ProcessReason.ESCALATED,
            "process_reason",
        )
        if target not in WAITING_PROCESS_REASONS:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="process_reason is not an escalation reason",
                status_code=422,
            )
        self._apply_transition(
            record,
            event=WorkOrderEvent.ESCALATE,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            process_reason=target,
            reason_code=reason[:64],
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_PROCESS_UPDATED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "status": record.status,
                "process_reason": record.process_reason,
            },
        )
        return record

    def _lock_assignee_action(
        self,
        actor: Principal,
        work_order_id: int,
        version: int | None,
        *,
        takeover: bool,
    ) -> tuple[Principal, WorkOrder, WorkOrderAssignment, Employee]:
        current = self._reload_actor(actor)
        record = self._lock_work_order(work_order_id)
        self._assert_version(record, version)
        RbacService.require_permission(
            current,
            WORK_ORDER_PROCESS,
            self._property_scope(record),
        )
        if record.status in {
            WorkOrderStatus.CLOSED.value,
            WorkOrderStatus.CANCELLED.value,
            WorkOrderStatus.COMPLETED.value,
        }:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order is already finished",
                status_code=409,
            )
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Work order has no active assignee",
                status_code=403,
            )
        employee = self._require_actor_employee(current)
        can_takeover = current.has_permission(
            WORK_ORDER_DISPATCH,
            self._property_scope(record),
        ) or current.has_permission(
            WORK_ORDER_DECIDE,
            self._property_scope(record),
        )
        if employee.id != assignment.employee_id and not (takeover and can_takeover):
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Caller is not the current assignee",
                status_code=403,
            )
        if employee.id != assignment.employee_id:
            assigned = self._employees.get_employee(assignment.employee_id)
            if assigned is None:
                raise AppError(
                    code=WorkOrderErrorCode.NOT_ASSIGNEE,
                    message="Caller is not the current assignee",
                    status_code=403,
                )
            employee = assigned
        return current, record, assignment, employee

    def _lock_work_order(self, work_order_id: int) -> WorkOrder:
        self._require_positive_id(work_order_id, "work_order_id")
        record = self._repository.get_work_order_for_update(work_order_id)
        if record is None:
            self._raise_not_found()
        return record

    def _apply_transition(
        self,
        record: WorkOrder,
        *,
        event: WorkOrderEvent,
        to_status: WorkOrderStatus,
        actor: Principal,
        actor_type: WorkOrderActorType,
        request_id: str,
        process_reason: ProcessReason | None = None,
        reason_code: str | None = None,
    ) -> None:
        from_status = WorkOrderStatus(record.status)
        try:
            require_legal_transition(
                from_status=from_status,
                to_status=to_status,
                event=event,
            )
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Work order transition is not allowed",
                status_code=409,
            ) from exc
        if to_status is WorkOrderStatus.PROCESSING:
            target_reason = process_reason or ProcessReason(record.process_reason)
        else:
            target_reason = ProcessReason.NONE
        record.status = to_status.value
        record.process_reason = target_reason.value
        record.version += 1
        self._write_log(
            record,
            actor=actor,
            actor_type=actor_type,
            from_status=from_status,
            to_status=to_status,
            event=event,
            request_id=request_id,
            reason_code=reason_code,
        )
        self._session.flush()

    def _write_log(
        self,
        record: WorkOrder,
        *,
        actor: Principal,
        actor_type: WorkOrderActorType,
        from_status: WorkOrderStatus | None,
        to_status: WorkOrderStatus,
        event: WorkOrderEvent,
        request_id: str,
        reason_code: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"event_code": event.value}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        self._repository.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=actor.user_id,
                actor_type=actor_type.value,
                from_status=None if from_status is None else from_status.value,
                to_status=to_status.value,
                process_reason=record.process_reason,
                event_code=event.value,
                request_id=request_id,
                payload_json=payload,
            )
        )

    def _create_assignment(
        self,
        record: WorkOrder,
        *,
        employee: Employee,
        actor: Principal,
        now: datetime,
        reason: str | None,
    ) -> WorkOrderAssignment:
        return self._repository.add_assignment(
            WorkOrderAssignment(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                employee_id=employee.id,
                assigned_by_user_id=actor.user_id,
                status=AssignmentStatus.ACTIVE.value,
                assigned_at=now,
                reason=reason,
            )
        )

    def _end_active_assignment(
        self,
        record: WorkOrder,
        *,
        now: datetime,
        status: AssignmentStatus,
        reason: str | None = None,
    ) -> None:
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            return
        assignment.status = status.value
        assignment.ended_at = now
        if reason is not None:
            assignment.reason = reason
        employee = self._employees.get_employee(assignment.employee_id)
        if employee is not None:
            self._adjust_load(record, employee, delta=-1)
        record.current_assignment_id = None
        self._session.flush()

    def _adjust_load(
        self,
        record: WorkOrder,
        employee: Employee,
        *,
        delta: int,
    ) -> None:
        property_record = self._repository.get_property(record.property_id)
        if property_record is None:
            return
        business_date = current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        daily = self._employees.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if daily is None:
            return
        daily.current_load = max(daily.current_load + delta, 0)
        self._session.flush()

    def _eligibility_error(
        self,
        record: WorkOrder,
        employee: Employee,
        *,
        ignore_capacity: bool = False,
    ) -> AppError | None:
        denied = AppError(
            code=WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE,
            message="Employee is not eligible for dispatch",
            status_code=409,
        )
        if employee.status != EmployeeStatus.ACTIVE.value:
            return denied
        if employee.tenant_id != record.tenant_id:
            return denied
        now = self._clock()
        assignment = self._employees.find_effective_assignment(
            employee_id=employee.id,
            property_id=record.property_id,
            now=now,
        )
        if assignment is None:
            return denied
        property_record = self._repository.get_property(record.property_id)
        if property_record is None:
            return denied
        business_date = current_business_date(
            property_record.timezone,
            clock=self._clock,
        )
        daily = self._employees.get_daily_status_for_update(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if daily is None or daily.on_duty != AttendanceOnDuty.ON_DUTY.value:
            return denied
        if not ignore_capacity and not employee_is_capacity_available(
            current_load=daily.current_load,
            max_capacity=employee.max_capacity,
        ):
            return denied
        return None

    def _attach_media(
        self,
        record: WorkOrder,
        *,
        actor: Principal,
        file_ids: tuple[int, ...],
        taken_phase: MediaTakenPhase,
    ) -> None:
        seen: set[int] = set()
        for file_id in file_ids:
            self._require_positive_id(file_id, "file_ids")
            if file_id in seen:
                continue
            seen.add(file_id)
            file_object = self._repository.get_file(file_id)
            if file_object is None or file_object.tenant_id != record.tenant_id:
                raise AppError(
                    code=FileErrorCode.NOT_FOUND,
                    message="File was not found",
                    status_code=404,
                )
            if (
                file_object.uploader_user_id != actor.user_id
                and file_object.property_id not in {None, record.property_id}
            ):
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Permission denied",
                    status_code=403,
                )
            kind = _ALLOWED_MIME.get(file_object.mime_type)
            if kind is None:
                raise AppError(
                    code=FileErrorCode.UNSUPPORTED_TYPE,
                    message="File type is not allowed",
                    status_code=422,
                )
            self._repository.add_media(
                WorkOrderMedia(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    kind=kind.value,
                    file_id=file_object.id,
                    taken_phase=taken_phase.value,
                    uploader_user_id=actor.user_id,
                )
            )

    def _authorize_read(self, actor: Principal, record: WorkOrder) -> None:
        if record.reporter_user_id == actor.user_id:
            return
        scope = self._property_scope(record)
        if actor.has_permission(WORK_ORDER_DISPATCH, scope) or actor.has_permission(
            WORK_ORDER_DECIDE, scope
        ):
            RbacService.require_permission(actor, WORK_ORDER_READ, scope)
            return
        if actor.has_permission(WORK_ORDER_READ, scope):
            assignment = self._repository.get_active_assignment(record.id)
            employee = self._employees.get_employee_for_user(actor.user_id)
            if (
                assignment is not None
                and employee is not None
                and assignment.employee_id == employee.id
            ):
                return
        raise AppError(
            code=IamErrorCode.FORBIDDEN,
            message="Permission denied",
            status_code=403,
        )

    def _require_reporter(self, actor: Principal, record: WorkOrder) -> None:
        if record.reporter_user_id != actor.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        RbacService.require_permission(
            actor,
            WORK_ORDER_READ,
            self._property_scope(record),
        )

    def _require_actor_employee(self, actor: Principal) -> Employee:
        employee = self._employees.get_employee_for_user(actor.user_id)
        if employee is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_ASSIGNEE,
                message="Caller is not the current assignee",
                status_code=403,
            )
        return employee

    def _stop_sla(self, record: WorkOrder) -> None:
        clock = self._repository.get_sla_clock(record.id)
        if clock is not None:
            clock.state = SlaClockState.STOPPED.value

    def _property_scope(self, record: WorkOrder) -> OrganizationScope:
        return OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )

    def _actor_type(
        self,
        actor: Principal,
        record: WorkOrder,
    ) -> WorkOrderActorType:
        if record.reporter_user_id == actor.user_id:
            return WorkOrderActorType.RESIDENT
        scope = self._property_scope(record)
        if actor.has_permission(WORK_ORDER_DECIDE, scope):
            return WorkOrderActorType.SUPERVISOR
        return WorkOrderActorType.STAFF

    def _notify(
        self,
        *,
        tenant_id: int,
        property_id: int,
        user_id: int,
        title: str,
        body: str,
        resource_type: str,
        resource_id: int,
    ) -> None:
        from app.service.notification import NotificationService

        NotificationService(self._session, settings=self._settings).enqueue(
            tenant_id=tenant_id,
            property_id=property_id,
            user_id=user_id,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def _actor_can_see_file_tenant(self, actor: Principal, tenant_id: int) -> bool:
        for grant in actor.grants:
            if grant.scope.scope_type == ScopeType.SYSTEM:
                return True
            if grant.scope.tenant_id == tenant_id:
                return True
        return False

    def _reload_actor(self, actor: Principal) -> Principal:
        self._require_active_actor(actor)
        try:
            current = self._principal_loader(actor.user_id)
        except AppError as exc:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            ) from exc
        if current.user_id != actor.user_id or current.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        return current

    def _execute(self, operation: Callable[[], T], *, composing: bool = False) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            return runner(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            self._map_insert_error(exc)
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order operation could not be completed",
                status_code=500,
            ) from exc

    def _map_insert_error(self, error: IntegrityError) -> None:
        arguments = getattr(error.orig, "args", ())
        number = arguments[0] if arguments else None
        message = str(arguments[1]).casefold() if len(arguments) > 1 else ""
        if number == 1062:
            if any(name.casefold() in message for name in _ACTIVE_ASSIGNMENT_UNIQUE):
                raise AppError(
                    code=WorkOrderErrorCode.ALREADY_ASSIGNED,
                    message="Work order is already assigned",
                    status_code=409,
                ) from error
            if any(name.casefold() in message for name in _IDEMPOTENCY_UNIQUE):
                raise AppError(
                    code=WorkOrderErrorCode.IDEMPOTENCY_CONFLICT,
                    message="Idempotency key was reused with a different request",
                    status_code=409,
                ) from error
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            ) from error
        if number == 1452 and any(
            name.casefold() in message for name in _HOUSE_SCOPE_FK
        ):
            raise AppError(
                code=WorkOrderErrorCode.SCOPE_MISMATCH,
                message="House does not belong to the declared property",
                status_code=422,
            ) from error
        if number == 1213:
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            ) from error
        raise AppError(
            code=WorkOrderErrorCode.CONFLICT,
            message="Work order conflicted with current data",
            status_code=409,
        ) from error

    def _append_audit(self, **values: object) -> None:
        payload = dict(values)
        payload.setdefault("scope_type", ScopeType.PROPERTY)
        try:
            self._audit_service.append(**payload)
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order audit could not be written",
                status_code=500,
            ) from exc

    @staticmethod
    def _assert_version(record: WorkOrder, version: int | None) -> None:
        if version is not None and version != record.version:
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order version conflicted",
                status_code=409,
            )

    @staticmethod
    def _parse_enum(value, enum_cls, default, field: str):
        if value is None or value == "":
            return default
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message=f"{field} is invalid",
                status_code=422,
                details={"field": field},
            ) from exc

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _request_hash(
        *,
        house_id: int,
        text: str | None,
        file_ids: tuple[int, ...],
        category: str | None,
        priority: str | None,
        severity: str | None,
    ) -> str:
        payload = "|".join(
            (
                str(house_id),
                text or "",
                ",".join(str(item) for item in file_ids),
                category or "",
                priority or "",
                severity or "",
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > 128
        ):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Idempotency-Key is required",
                status_code=422,
            )

    @staticmethod
    def _require_active_actor(actor: Principal) -> None:
        if (
            not isinstance(actor, Principal)
            or actor.user_id <= 0
            or actor.status != "ACTIVE"
        ):
            raise AppError(
                code=AuthErrorCode.USER_DISABLED
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=401
                if getattr(actor, "status", None) not in {None, "ACTIVE"}
                else 403,
            )

    @staticmethod
    def _require_positive_id(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message=f"{field} must be a positive integer",
                status_code=422,
                details={"field": field},
            )

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if (
            not isinstance(request_id, str)
            or not request_id
            or request_id != request_id.strip()
            or len(request_id) > 128
        ):
            raise AppError(
                code=WorkOrderErrorCode.CONFLICT,
                message="Work order conflicted with current data",
                status_code=409,
            )

    @staticmethod
    def _page_limit(limit: int) -> int:
        if limit < 1:
            return 1
        return min(limit, 100)

    @staticmethod
    def _raise_not_found() -> None:
        raise AppError(
            code=WorkOrderErrorCode.NOT_FOUND,
            message="Work order was not found",
            status_code=404,
        )
