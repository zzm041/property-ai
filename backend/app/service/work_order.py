"""WorkOrder create, FSM, manual dispatch, processing, and receipt use cases."""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import retryable_mysql_transaction_error_code
from app.core.errors import (
    AiErrorCode,
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
from app.domain.ai import FilterReason, WorkOrderAnalysisOutput, capability_filter_reason, category_duty, eligibility_denial_details, map_analysis_output, ordinary_maintenance
from app.domain.employee import DISPATCH_STAFF_ROLES, AttendanceOnDuty, EmployeeStatus
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.work_order_visibility import resident_media_visible
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
    is_legal_transition,
    require_legal_transition,
    sla_is_overdue,
    sla_warning_lead_minutes,
    sla_windows_for_priority,
    staff_may_submit_resolved_receipt,
)
from app.model.employee import Employee
from app.model.work_order import (
    FileObject,
    IdempotencyRecord,
    SlaClock,
    SlaPolicyVersion,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderContent,
    WorkOrderFeedback,
    WorkOrderLog,
    WorkOrderMedia,
    WorkOrderProcess,
    WorkOrderReceipt,
    WorkOrderSlaApplication,
)
from app.provider.factory import build_file_scanner_provider, build_storage_provider
from app.provider.file_scanner import FileScannerProvider
from app.provider.storage import StorageProvider
from app.repository.employee import EmployeeRepository
from app.repository.ai import AiRepository
from app.repository.identity import IdentityRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService
from app.service.sla import (
    reset_assignment_sla_deadlines,
    reset_response_sla_deadline,
)

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
_ALLOWED_EXTENSIONS = {
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "image/webp": frozenset({".webp"}),
    "audio/mpeg": frozenset({".mp3"}),
    "audio/mp4": frozenset({".m4a", ".mp4"}),
    "audio/wav": frozenset({".wav"}),
    "audio/webm": frozenset({".webm"}),
}


def _content_matches_mime(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    if mime_type == "audio/mpeg":
        return content.startswith(b"ID3") or (
            len(content) >= 2
            and content[0] == 0xFF
            and content[1] & 0xE0 == 0xE0
        )
    if mime_type == "audio/mp4":
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if mime_type == "audio/wav":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WAVE"
        )
    if mime_type == "audio/webm":
        return content.startswith(b"\x1aE\xdf\xa3")
    return False


def _filename_matches_mime(mime_type: str, filename: str | None) -> bool:
    if not filename:
        return True
    return Path(filename).suffix.casefold() in _ALLOWED_EXTENSIONS.get(
        mime_type,
        frozenset(),
    )


def _registration_content_hash(record: FileObject) -> str:
    return sha256(
        f"{record.storage_key}:{record.mime_type}:{record.size_bytes}".encode()
    ).hexdigest()
_ACTIVE_ASSIGNMENT_UNIQUE = frozenset(
    {"uq_work_order_assignments_active_work_order"}
)
_IDEMPOTENCY_UNIQUE = frozenset(
    {
        # D1 renamed the constraint when the scope became (user_id, key); both
        # names are accepted so an unmigrated deployment still maps the race.
        "uq_idempotency_records_user_key",
        "uq_idempotency_records_key",
    }
)
_HOUSE_SCOPE_FK = frozenset({"fk_work_orders_house_property_tenant_houses"})
_QUEUE_STATUSES = tuple(
    status.value for status in PENDING_DISPATCH_STATUSES
) + (
    WorkOrderStatus.PROCESSING.value,
)
_ESCALATION_REASONS = frozenset(
    reason.value for reason in WAITING_PROCESS_REASONS
)
_STAFF_ESCALATION_REASONS = frozenset(
    {
        ProcessReason.WAITING_OWNER,
        ProcessReason.WAITING_PARTS,
        ProcessReason.INTERNAL_UNRESOLVED,
        ProcessReason.ESCALATED,
    }
)


def eligibility_denied(
    *,
    reason: str,
    required_duty: str | None = None,
    required_skill: str | None = None,
) -> AppError:
    """One 409 shape for every dispatch denial, now carrying its reason.

    PA-127 F1: the two scope pre-checks that run before `_eligibility_error`
    (unknown employee, employee from another tenant) used to raise this code
    with an empty `details` payload, so a rejected manual dispatch could not be
    told apart from a skill or roster gate.
    """

    return AppError(
        code=WorkOrderErrorCode.EMPLOYEE_NOT_ELIGIBLE,
        message="Employee is not eligible for dispatch",
        status_code=409,
        details=eligibility_denial_details(
            reason,
            required_duty=required_duty,
            required_skill=required_skill,
        ),
    )


def _employee_scope_denial(
    employee: Employee | None,
    *,
    tenant_id: int,
) -> AppError | None:
    """Deny before any roster lookup when the assignee is not this tenant's.

    PA-134: this used to answer ``TENANT_MISMATCH`` for a row owned by another
    tenant, which turned the 409 body into an oracle: an operator could walk
    employee ids and learn which of them exist in other tenants. Both cases now
    answer the single ``EMPLOYEE_NOT_FOUND`` reason, so the two responses are
    byte-for-byte identical; the distinction survives only in the server log
    (see ``WorkOrderService._log_assignment_lookup_missed``).

    The tenant check stays as defence in depth even though the caller loads the
    employee through a tenant-scoped query and can therefore only see ``None``.
    """

    if employee is None:
        return eligibility_denied(reason="EMPLOYEE_NOT_FOUND")
    if employee.tenant_id != tenant_id:
        return eligibility_denied(reason="EMPLOYEE_NOT_FOUND")
    return None


class WorkOrderService:
    """Own WorkOrder transactions; repositories never commit."""

    def __init__(
        self,
        session: Session,
        repository: WorkOrderRepository | None = None,
        employee_repository: EmployeeRepository | None = None,
        resident_repository: ResidentBindingRepository | None = None,
        audit_service: AuditService | None = None,
        ai_repository: AiRepository | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        storage: StorageProvider | None = None,
        file_scanner: FileScannerProvider | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._repository = repository or WorkOrderRepository(session)
        self._employees = employee_repository or EmployeeRepository(session)
        self._residents = resident_repository or ResidentBindingRepository(session)
        self._audit_service = audit_service or AuditService(session)
        self._analysis = ai_repository or AiRepository(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal
        self._settings = settings or get_settings()
        self._storage = storage or build_storage_provider(self._settings)
        self._file_scanner = file_scanner or build_file_scanner_provider(
            self._settings
        )
        self._clock = clock

    @property
    def max_upload_bytes(self) -> int:
        """Maximum accepted upload payload, shared by registration and streaming."""

        return self._settings.upload_max_mb * 1024 * 1024

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
        normalized_mime = mime_type.strip().casefold()
        if not _filename_matches_mime(normalized_mime, original_name):
            raise AppError(
                code=FileErrorCode.UNSUPPORTED_TYPE,
                message="File extension does not match the declared type",
                status_code=422,
            )
        max_bytes = self.max_upload_bytes
        if size_bytes <= 0 or size_bytes > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        property_id = claimed_property_id
        tenant_id = claimed_tenant_id
        stored_holder: dict[str, object] = {}

        def _persist() -> FileObject:
            from app.service.identity_write_guard import lock_active_identity

            lock_active_identity(self._session, actor.user_id)
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
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                filename=original_name,
                tenant_id=resolved_tenant_id,
                property_id=resolved_property_id,
            )
            stored_holder["stored"] = stored
            record = FileObject(
                tenant_id=resolved_tenant_id,
                property_id=resolved_property_id,
                uploader_user_id=actor.user_id,
                content_hash="",
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                storage_key=stored.storage_key,
                original_name=original_name,
                scanner_status=stored.scanner_status,
            )
            record.content_hash = _registration_content_hash(record)
            record = self._repository.add_file(record)
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
        return self._execute(
            lambda: self._put_file_content(
                actor=actor,
                file_id=file_id,
                content=content,
                request_id=request_id,
            )
        )

    def _put_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        content: bytes,
        request_id: str,
    ) -> FileObject:
        current = self._reload_actor(actor)
        self._validate_request_id(request_id)
        self._require_positive_id(file_id, "file_id")
        record = self._repository.get_file_for_update(file_id)
        if record is None or not self._actor_can_see_file_tenant(
            current, record.tenant_id
        ):
            raise AppError(
                code=FileErrorCode.NOT_FOUND,
                message="File was not found",
                status_code=404,
            )
        if record.uploader_user_id != current.user_id:
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        max_bytes = self.max_upload_bytes
        if len(content) > max_bytes:
            raise AppError(
                code=FileErrorCode.TOO_LARGE,
                message="File exceeds the upload size limit",
                status_code=422,
            )
        if not content or len(content) != record.size_bytes:
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="Uploaded content size does not match registration",
                status_code=422,
            )
        if not _content_matches_mime(record.mime_type, content):
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="Uploaded content does not match the declared type",
                status_code=422,
            )
        from app.provider.errors import (
            ProviderInvalidOutputError,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )

        placeholder_hash = _registration_content_hash(record)
        try:
            existing = self._storage.get_object(record.storage_key)
        except ProviderInvalidOutputError:
            if record.content_hash != placeholder_hash:
                logger.error(
                    "stored_object_missing",
                    file_id=record.id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                raise AppError(
                    code=FileErrorCode.STORAGE_UNAVAILABLE,
                    message="Stored file content is unavailable",
                    status_code=503,
                )
            existing = None
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        if existing is not None:
            existing_hash = sha256(existing).hexdigest()
            if (
                len(existing) != record.size_bytes
                or not _content_matches_mime(record.mime_type, existing)
                or record.content_hash not in {placeholder_hash, existing_hash}
            ):
                logger.error(
                    "stored_object_integrity_failed",
                    file_id=record.id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                raise AppError(
                    code=FileErrorCode.STORAGE_UNAVAILABLE,
                    message="Stored file failed integrity validation",
                    status_code=503,
                )
            if existing != content:
                raise AppError(
                    code=FileErrorCode.CONFLICT,
                    message="File content is immutable after upload",
                    status_code=409,
                )
            record.content_hash = existing_hash
            if record.scanner_status == "pending":
                record.scanner_status = self._file_scanner.scan(
                    content=existing,
                    mime_type=record.mime_type,
                )
            self._session.flush()
            return record

        try:
            self._storage.put_object(record.storage_key, content, record.mime_type)
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        record.content_hash = sha256(content).hexdigest()
        if record.scanner_status == "pending":
            record.scanner_status = self._file_scanner.scan(
                content=content,
                mime_type=record.mime_type,
            )
        self._session.flush()
        return record

    def get_file_content(
        self,
        *,
        actor: Principal,
        file_id: int,
        audience: str = "resident",
    ) -> tuple[FileObject, bytes]:
        current = self._reload_actor(actor)
        self._require_positive_id(file_id, "file_id")
        record = self._repository.get_file(file_id)
        if record is None:
            self._raise_file_not_found()
        self._validate_audience(audience)
        if not self._file_is_visible(current, record, audience=audience):
            self._raise_file_not_found()
        return record, self._read_stored_file(record)

    def _file_is_visible(self, current: Principal, record: FileObject, *, audience: str) -> bool:
        """Metadata-only gate shared by downloads and new media associations."""
        orders = self._repository.list_work_orders_for_file(record.id)
        allowed = not orders and record.uploader_user_id == current.user_id
        for work_order in orders:
            try:
                self._authorize_read(current, work_order, allow_reporter=audience == "resident")
            except AppError:
                continue
            if audience == "property":
                allowed = True
            else:
                receipt_ids = {item.id for item in self._repository.list_receipts(work_order.id)}
                allowed = any(
                    item.file_id == record.id and resident_media_visible(
                        phase=item.taken_phase, receipt_id=item.receipt_id, receipt_ids=receipt_ids,
                    )
                    for item in self._repository.list_media(work_order.id)
                )
            if allowed:
                break
        return bool(allowed)

    def _read_stored_file(self, record: FileObject) -> bytes:
        if record.scanner_status not in {"clean", "skipped_dev"}:
            # PA-163: `details` used to be empty here, so a deployment with no
            # scanner produced a bare 409 that named neither the reason nor the
            # state of the file. The message stays the same for existing clients.
            raise AppError(
                code=FileErrorCode.INVALID_INPUT,
                message="File content is not ready for use",
                status_code=409,
                details={
                    "reason": "MEDIA_NOT_READY",
                    "scanner_status": record.scanner_status,
                },
            )

        from app.provider.errors import (
            ProviderInvalidOutputError,
            ProviderTimeoutError,
            ProviderUnavailableError,
        )

        try:
            content = self._storage.get_object(record.storage_key)
        except ProviderInvalidOutputError as exc:
            raise AppError(
                code=FileErrorCode.NOT_FOUND,
                message="File content was not found",
                status_code=404,
            ) from exc
        except (ProviderTimeoutError, ProviderUnavailableError) as exc:
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Object storage is unavailable",
                status_code=503,
            ) from exc
        if (
            len(content) != record.size_bytes
            or sha256(content).hexdigest() != record.content_hash
            or not _content_matches_mime(record.mime_type, content)
        ):
            logger.error(
                "stored_object_integrity_failed",
                file_id=record.id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
            raise AppError(
                code=FileErrorCode.STORAGE_UNAVAILABLE,
                message="Stored file failed integrity validation",
                status_code=503,
            )
        return content

    def list_my_work_orders(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        current = self._reload_actor(actor)
        page_limit = self._page_limit(limit)
        return self._repository.list_for_reporter(
            user_id=current.user_id,
            after_id=after_id,
            # The resident endpoint consumes one look-ahead record so it can
            # distinguish an exact final page from a page that has more data.
            limit=page_limit + 1,
        )

    def create_work_order(
        self,
        *,
        actor: Principal,
        house_id: int,
        request_id: str,
        idempotency_key: str,
        contact_phone: str | None = None,
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
        from app.domain.phone import normalize_phone

        contact = normalize_phone(contact_phone)
        if contact is None or not contact.isascii():
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT,
                           message="A valid contact telephone is required", status_code=422)
        record, created = self._execute(
            lambda: self._create_work_order(
                actor=actor,
                house_id=house_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
                contact_phone=contact,
                text=text,
                file_ids=file_ids,
                category=category,
                priority=priority,
                severity=severity,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            ),
            analysis_request_id=request_id,
        )
        if created:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record

    def get_contact(self, *, actor: Principal, work_order_id: int, reveal: bool, request_id: str) -> dict:
        """Explicit, scoped plaintext access; never returned by common list DTOs."""
        from app.domain.phone import mask_phone

        self._require_positive_id(work_order_id, "work_order_id")
        self._validate_request_id(request_id)
        def operation():
            current = self._reload_actor(actor)
            record = self._lock_work_order(work_order_id)
            scope = self._property_scope(record)
            scoped = Principal(current.user_id, current.status, current.display_name,
                               tuple(g for g in current.grants if g.scope.scope_type is not ScopeType.SYSTEM))
            allowed = record.reporter_user_id == current.user_id
            if not allowed:
                management = scoped.has_permission(WORK_ORDER_READ, scope) and (
                    scoped.has_permission(WORK_ORDER_DISPATCH, scope) or scoped.has_permission(WORK_ORDER_DECIDE, scope))
                employee = self._employees.get_employee_for_user(current.user_id)
                assignment = self._repository.get_active_assignment(record.id)
                responsible = (
                    scoped.has_permission(WORK_ORDER_READ, scope) and employee is not None
                    and employee.status == EmployeeStatus.ACTIVE.value
                    and employee.tenant_id == record.tenant_id and assignment is not None
                    and assignment.employee_id == employee.id
                    and self._employees.find_effective_assignment(
                        employee_id=employee.id, property_id=record.property_id, now=self._clock()) is not None
                )
                allowed = management or responsible
            if not allowed:
                raise AppError(code=IamErrorCode.FORBIDDEN, message="Contact access denied", status_code=403)
            phone = record.contact_phone_e164
            if reveal and phone:
                self._append_audit(action=AuditAction.WORK_ORDER_CONTACT_READ,
                    resource_type=AuditResourceType.WORK_ORDER, actor_user_id=current.user_id,
                    resource_id=record.id, request_id=request_id, scope_type=ScopeType.PROPERTY,
                    tenant_id=record.tenant_id, property_id=record.property_id,
                    details={"work_order_id": record.id})
            return {"collected": bool(phone), "masked_phone": mask_phone(phone) if phone else None,
                    "phone": phone if reveal else None}
        return self._execute(operation)

    def _enqueue_analysis(self, work_order_id: int, request_id: str, *, version: int | None = None) -> None:
        if self._settings.durable_tasks_enabled:
            return  # Already persisted in the business transaction; publisher is the backstop.
        if not self._settings.should_enqueue_ai_on_create:
            return
        from app.worker.enqueue import enqueue_celery_task
        from app.worker.tasks import analyze_work_order_task

        queued = enqueue_celery_task(
            settings=self._settings,
            celery_task=analyze_work_order_task,
            args=(work_order_id, request_id),
            event="ai_enqueue_failed",
            extra={"work_order_id": work_order_id},
            headers={"analysis_expected_version": version} if version is not None else None,
        )
        if not queued and self._settings.app_env == "production":
            # PA-104-A: never commit business state whose analysis never reached a queue.
            # RAISE means the owned transaction rolls back; no orphan "DB row / no task" state.
            raise AppError(
                code=AiErrorCode.ENQUEUE_FAILED,
                message="Analysis queue unavailable; request was not committed",
                status_code=503,
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
        audience: str = "resident",
    ) -> tuple[
        WorkOrder,
        WorkOrderContent | None,
        tuple[WorkOrderMedia, ...],
        tuple[WorkOrderAssignment, ...],
        tuple[WorkOrderProcess, ...],
        tuple[WorkOrderReceipt, ...],
        tuple[WorkOrderFeedback, ...],
        tuple[WorkOrderLog, ...],
        dict[str, object],
    ]:
        self._validate_audience(audience)
        record = self.get_work_order(actor=actor, work_order_id=work_order_id)
        if audience == "property":
            self._authorize_read(self._reload_actor(actor), record, allow_reporter=False)
        analysis = self._analysis.get_current_analysis(record.id)
        display_context = self._repository.get_display_context(record)
        display_context["analysis_status"] = None if analysis is None else analysis.status
        if (analysis is not None and analysis.status == "LOW_CONFIDENCE"
                and getattr(analysis, "error_code", None) == AiErrorCode.OUT_OF_SCOPE):
            display_context["analysis_disposition"] = "OUT_OF_SCOPE_REQUEST"
        return (
            record,
            self._repository.get_content(record.id),
            self._repository.list_media(record.id),
            self._repository.list_assignments(record.id),
            self._repository.list_processes(record.id),
            self._repository.list_receipts(record.id),
            self._repository.list_feedbacks(record.id),
            self._repository.list_logs(record.id),
            display_context,
        )

    def diagnose_work_order(self, *, actor: Principal, work_order_id: int,
                            property_id: int, version: int):
        """Read existing evidence only. No inference, mutation, or scheduling."""
        from app.domain.diagnosis import diagnosis_items
        from app.schema.work_order import WorkOrderDiagnosisData

        self._require_positive_id(work_order_id, 'work_order_id')
        self._require_positive_id(property_id, 'property_id')
        self._require_positive_id(version, 'version')
        current = self._reload_actor(actor)
        record = self._repository.get_work_order(work_order_id)
        if record is None:
            self._raise_not_found()
        scope = OrganizationScope(ScopeType.PROPERTY, record.tenant_id, record.property_id)
        RbacService.require_permission(current, WORK_ORDER_DISPATCH, scope)
        if record.property_id != property_id:
            raise AppError(code=WorkOrderErrorCode.SCOPE_MISMATCH, message='Work order scope changed', status_code=422)
        self._assert_version(record, version)
        observed_at = self._clock()
        analysis = self._analysis.get_current_analysis(record.id)
        process = self._repository.get_current_process(record.id)
        wait = self._repository.latest_dispatch_wait(work_order_id=record.id,
            tenant_id=record.tenant_id, property_id=record.property_id)
        payload = None if wait is None else wait.payload_json
        wait_reason = payload.get('reason_code') if isinstance(payload, dict) else None
        if not isinstance(wait_reason, str):
            wait_reason = None
        facts, actions = diagnosis_items(status=record.status, process_reason=record.process_reason,
            analysis_status=None if analysis is None else analysis.status,
            analysis_error_code=None if analysis is None else getattr(analysis, 'error_code', None),
            cycle_no=None if process is None else process.cycle_no,
            wait_reason=wait_reason, overdue=self.is_overdue(record, observed_at))
        sources = {'work_order': record, 'analysis': analysis,
                   'work_order_log': wait, 'work_order_process': process}
        return WorkOrderDiagnosisData(work_order_id=record.id, property_id=record.property_id,
            version=record.version, observed_at=observed_at,
            facts=[dict(code=code, text=text, source_type=kind, source_id=sources[kind].id,
                        recorded_at=getattr(sources[kind], 'updated_at', None) if kind == 'work_order'
                        else getattr(sources[kind], 'created_at', None)) for code, text, kind in facts],
            suggested_actions=actions)

    def list_supervisor_queue(
        self,
        *,
        actor: Principal,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        claimed_tenant_id: int | None = None,
        view: str = "dispatch",
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
        if view not in {"dispatch", "unstarted"}:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Unknown work-order queue view",
                status_code=422,
            )
        # Derive the management list from the same frozen FSM used by cancel.
        statuses = _QUEUE_STATUSES if view == "dispatch" else tuple(
            status.value for status in WorkOrderStatus
            if is_legal_transition(
                from_status=status,
                to_status=WorkOrderStatus.CANCELLED,
                event=WorkOrderEvent.CANCEL,
            )
        )
        records = self._repository.list_queue_for_property(
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
            statuses=statuses,
            after_id=after_id,
            limit=self._page_limit(limit),
        )
        if view == "unstarted":
            return records
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
        if employee.status != EmployeeStatus.ACTIVE.value:
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        property_ids = tuple(
            property_id
            for property_id in self._employees.list_active_property_ids_for_employee(
                employee_id=employee.id, now=self._clock(),
            )
            if current.has_permission(
                WORK_ORDER_READ,
                OrganizationScope(ScopeType.PROPERTY, tenant_id=employee.tenant_id, property_id=property_id),
            )
        )
        if not property_ids:
            return ()
        return self._repository.list_for_assignee(
            employee_id=employee.id,
            tenant_id=employee.tenant_id,
            property_ids=property_ids,
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
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        record = self._execute(
            lambda: self._append_content(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                text=text,
                file_ids=file_ids,
                taken_phase=taken_phase,
                version=version,
            ),
            analysis_request_id=request_id,
        )
        if record.status == WorkOrderStatus.PENDING_ANALYSIS.value:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record

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
        record = self._execute(
            lambda: self._cancel(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason,
                version=version,
            )
        )

        from app.worker.enqueue import schedule_dispatch_recovery
        schedule_dispatch_recovery(self._settings)
        return record

    def confirm(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        record = self._execute(
            lambda: self._confirm(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                version=version,
            )
        )

        from app.worker.enqueue import schedule_dispatch_recovery
        schedule_dispatch_recovery(self._settings)
        return record

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
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 255:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="reason must contain 1 to 255 characters",
                status_code=422,
            )
        record = self._execute(
            lambda: self._reopen(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            ),
            analysis_request_id=request_id,
        )
        if record.status == WorkOrderStatus.PENDING_ASSIGNMENT.value:
            self._enqueue_analysis(record.id, request_id, version=record.version)
        return record

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
        record = self._execute(
            lambda: self._reject(
                actor=actor,
                work_order_id=work_order_id,
                request_id=request_id,
                reason=reason.strip(),
                version=version,
            ),
            analysis_request_id=request_id,
        )
        self._enqueue_analysis(record.id, request_id, version=record.version)
        return record

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
        before_file_ids: tuple[int, ...] = (),
        after_file_ids: tuple[int, ...] = (),
        file_ids: tuple[int, ...] = (),
        version: int | None = None,
        handling_mode: str | None = None,
        handling_note: str | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        from app.domain.receipt_handling import normalize_handling

        try:
            handling_mode, handling_note = normalize_handling(handling_mode, handling_note)
        except ValueError as exc:
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT, message=str(exc), status_code=422) from exc
        if not problem_type.strip() or not method.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt required fields are missing",
                status_code=422,
            )
        normalized_before_ids = tuple(dict.fromkeys(before_file_ids))
        normalized_after_ids = tuple(dict.fromkeys((*after_file_ids, *file_ids)))
        if set(normalized_before_ids).intersection(normalized_after_ids):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media cannot be both before and after",
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
                before_file_ids=normalized_before_ids,
                after_file_ids=normalized_after_ids,
                version=version,
                handling_mode=handling_mode,
                handling_note=handling_note,
            )
        )

    def append_receipt_media(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        receipt_id: int,
        request_id: str,
        before_file_ids: tuple[int, ...] = (),
        after_file_ids: tuple[int, ...] = (),
        version: int | None = None,
    ) -> WorkOrder:
        self._validate_request_id(request_id)
        normalized_before_ids = tuple(dict.fromkeys(before_file_ids))
        normalized_after_ids = tuple(dict.fromkeys(after_file_ids))
        if not normalized_before_ids and not normalized_after_ids:
            raise AppError(
                code=WorkOrderErrorCode.CONTENT_REQUIRED,
                message="Receipt media is required",
                status_code=422,
            )
        if set(normalized_before_ids).intersection(normalized_after_ids):
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media cannot be both before and after",
                status_code=422,
            )
        return self._execute(
            lambda: self._append_receipt_media(
                actor=actor,
                work_order_id=work_order_id,
                receipt_id=receipt_id,
                request_id=request_id,
                before_file_ids=normalized_before_ids,
                after_file_ids=normalized_after_ids,
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
        contact_phone: str,
        text: str | None,
        file_ids: tuple[int, ...],
        category: str | None,
        priority: str | None,
        severity: str | None,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> tuple[WorkOrder, bool]:
        from app.service.identity_write_guard import lock_active_identity

        lock_active_identity(self._session, actor.user_id)
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
            contact_phone=contact_phone,
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
            return record, False
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
        versioning_available = self._sla_versioning_available()
        sla_version = None
        accept_minutes = respond_minutes
        warning_delta = sla_warning_lead_minutes(complete_minutes)
        escalation_minutes = 0
        if versioning_available and policy is not None:
            sla_version = self._session.scalar(
                select(SlaPolicyVersion)
                .where(SlaPolicyVersion.policy_id == policy.id)
                .order_by(SlaPolicyVersion.version_no.desc(), SlaPolicyVersion.id.desc())
                .limit(1)
            )
            if sla_version is not None:
                accept_minutes = sla_version.accept_minutes
                respond_minutes = sla_version.respond_minutes
                complete_minutes = sla_version.complete_minutes
                warning_delta = sla_version.warning_lead_minutes
                escalation_minutes = sla_version.escalation_minutes
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
                contact_phone_e164=contact_phone,
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
        if versioning_available:
            self._session.add(
                WorkOrderSlaApplication(
                    work_order_id=record.id,
                    policy_version_id=None if sla_version is None else sla_version.id,
                    source=(
                        "CODE_DEFAULT"
                        if policy is None
                        else "TENANT" if policy.property_id is None else "PROPERTY"
                    ),
                    accept_minutes=accept_minutes,
                    respond_minutes=respond_minutes,
                    complete_minutes=complete_minutes,
                    warning_lead_minutes=warning_delta,
                    escalation_minutes=escalation_minutes,
                    applied_at=now,
                )
            )
        self._repository.add_sla_clock(
            SlaClock(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                accept_due_at=now + timedelta(minutes=accept_minutes),
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
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单已创建",
            body="工单已提交，系统正在分析并安排处理",
        )
        return record, True

    def _append_content(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        request_id: str,
        text: str | None,
        file_ids: tuple[int, ...],
        taken_phase: str | None,
        version: int | None = None,
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
        self._assert_version(record, version)
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
        phase = MediaTakenPhase.REPORT
        if taken_phase is not None:
            phase = self._parse_enum(
                taken_phase,
                MediaTakenPhase,
                MediaTakenPhase.REPORT,
                "taken_phase",
            )
            if phase is not MediaTakenPhase.REPORT:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="Resident append media must use the report phase",
                    status_code=422,
                )
        content = self._repository.get_content(record.id)
        if content is not None and cleaned is not None:
            notes = content.extra_notes or ""
            content.extra_notes = f"{notes}\n{cleaned}".strip() if notes else cleaned
        self._attach_media(record, actor=current, file_ids=file_ids, taken_phase=phase)
        # Content mutations participate in optimistic concurrency and make an
        # in-flight analysis snapshot stale before its result can be applied.
        record.version += 1
        scope_review_invalidated = False
        if record.status == WorkOrderStatus.PENDING_ANALYSIS.value:
            analysis = self._analysis.get_current_analysis_for_update(record.id)
            if (analysis is not None and analysis.status == "LOW_CONFIDENCE"
                    and analysis.error_code == AiErrorCode.OUT_OF_SCOPE):
                # A new resident version must not retain an old pure-scope
                # conclusion while its fresh analysis is queued. The original
                # reason and success audit remain available for history.
                analysis.status = "STALE"
                scope_review_invalidated = True
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.RESIDENT,
            from_status=WorkOrderStatus(record.status),
            to_status=WorkOrderStatus(record.status),
            event=WorkOrderEvent.APPEND,
            request_id=request_id,
            reason_code="SCOPE_REVIEW_INVALIDATED_BY_APPEND" if scope_review_invalidated else None,
            reason_detail=cleaned,
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
        scope = self._property_scope(record)
        is_reporter = record.reporter_user_id == current.user_id
        active_assignee_user_id = None
        if is_reporter:
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
            if record.status not in {
                WorkOrderStatus.PENDING_ANALYSIS.value,
                WorkOrderStatus.PENDING_ASSIGNMENT.value,
            }:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Resident may cancel only before assignment",
                    status_code=409,
                )
        else:
            # WORK_ORDER_CANCEL is also granted to residents so they can
            # withdraw their own pending reports.  It is not, by itself, an
            # ownership grant for another resident's work order.  Reuse the
            # existing read/assignment/management policy before allowing a
            # non-reporter cancellation.
            # An assigned STAFF who also has RESIDENT cancellation permission
            # must not combine those grants into manager powers over others.
            if not (
                current.has_permission(WORK_ORDER_DISPATCH, scope)
                or current.has_permission(WORK_ORDER_DECIDE, scope)
            ):
                raise AppError(
                    code=IamErrorCode.FORBIDDEN,
                    message="Manager permission is required to cancel another report",
                    status_code=403,
                )
            self._authorize_read(current, record)
            RbacService.require_permission(current, WORK_ORDER_CANCEL, scope)
            active_assignee_user_id = self._repository.get_active_assignee_user_id(
                record.id
            )
        self._assert_version(record, version)
        self._apply_transition(
            record,
            event=WorkOrderEvent.CANCEL,
            to_status=WorkOrderStatus.CANCELLED,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            reason_code="CANCELLED",
            reason_detail=self._optional_text(reason),
        )
        self._end_active_assignment(
            record,
            now=self._clock(),
            status=AssignmentStatus.ENDED,
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
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id, active_assignee_user_id),
            exclude_user_ids=(current.user_id,),
            title="工单已撤销",
            body="工单已撤销，请查看详情",
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
        self._require_reporter(current, record)
        self._assert_version(record, version)
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
        self._notify_users(
            record,
            user_ids=(
                record.reporter_user_id,
                *self._repository.list_sla_management_user_ids(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
            ),
            title="工单已完成",
            body="住户已确认处理结果，工单已完成",
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
        self._require_reporter(current, record)
        self._assert_version(record, version)
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
                reason_detail=reason,
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
                reason_detail=reason,
            )
        record.resolved_at = None
        self._resume_reopened_sla(record)
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
        if eligible and employee is not None:
            recipients: Iterable[int | None] = (employee.user_id,)
        else:
            recipients = self._repository.list_sla_management_user_ids(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
        self._notify_users(
            record,
            user_ids=recipients,
            exclude_user_ids=(current.user_id,),
            title="工单已重开",
            body="住户已重开工单，请查看并继续处理",
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
        if self._repository.get_rating_for_update(
            work_order_id=record.id,
            actor_user_id=current.user_id,
        ) is not None:
            raise AppError(
                code=WorkOrderErrorCode.ALREADY_PROCESSED,
                message="Work order has already been rated",
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

    def _log_assignment_lookup_missed(
        self, employee_id: int, *, tenant_id: int
    ) -> None:
        """Record *why* the tenant-scoped assignee lookup came back empty.

        PA-134: the 409 body is deliberately identical for "no such id" and
        "id belongs to another tenant", so the reason is kept for internal
        diagnostics only and never leaves the process. The extra read runs on
        the denial path only.
        """

        foreign = self._employees.get_employee(employee_id) is not None
        logger.warning(
            "dispatch_employee_lookup_missed",
            employee_id=employee_id,
            tenant_id=tenant_id,
            error_reason="EMPLOYEE_FOREIGN" if foreign else "EMPLOYEE_NOT_FOUND",
        )

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
        RbacService.require_permission(
            current,
            WORK_ORDER_DISPATCH,
            self._property_scope(record),
        )
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
        self._assert_version(record, version)
        # Share the global assignment mutex with AUTO, even for authorized
        # manual intervention. No business scope is inferred from this lock.
        # PA-134: the tenant predicate is part of the locking read, so an id
        # from another tenant is indistinguishable from an unknown one here.
        employee = self._employees.get_employee_in_tenant(
            tenant_id=record.tenant_id,
            employee_id=employee_id,
            for_update=True,
        )
        if employee is None:
            self._log_assignment_lookup_missed(
                employee_id, tenant_id=record.tenant_id
            )
        # PA-127 F1: these two pre-checks used to raise the same bare 409 as
        # every other denial, so "unknown employee" and "employee belongs to
        # another tenant" were indistinguishable from a skill or roster gate.
        scope_denial = _employee_scope_denial(
            employee, tenant_id=record.tenant_id
        )
        if scope_denial is not None:
            raise scope_denial
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
            self._end_current_process(record, now=now)
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
        reset_assignment_sla_deadlines(
            self._repository,
            work_order=record,
            now=now,
        )
        self._adjust_load(record, employee, delta=1)
        from app.service.incident import IncidentCapacityService

        IncidentCapacityService(
            self._session,
            work_order_repository=self._repository,
        ).adopt_assignment(
            work_order=record,
            assignment=assignment,
        )
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
        self._notify_users(
            record,
            user_ids=(employee.user_id, record.reporter_user_id),
            title="工单已派单",
            body="工单已安排处理，请查看最新进度",
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
        RbacService.require_permission(
            current,
            WORK_ORDER_DECIDE,
            self._property_scope(record),
        )
        self._assert_version(record, version)
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
        cleaned_reason = self._optional_text(reason)
        if code in {SupervisorDecision.CONTINUE, SupervisorDecision.REASSIGN} and cleaned_reason is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message=f"reason is required for {code.value}",
                status_code=422,
            )
        active_assignee_user_id = None
        if code is SupervisorDecision.REASSIGN:
            if employee_id is None:
                now = self._clock()
                self._end_current_process(record, now=now)
                self._end_active_assignment(
                    record,
                    now=now,
                    status=AssignmentStatus.REASSIGNED,
                    reason=cleaned_reason,
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
                    reason=cleaned_reason,
                    version=record.version,
                    claimed_tenant_id=None,
                    claimed_property_id=None,
                )
        else:
            active_assignee_user_id = self._repository.get_active_assignee_user_id(
                record.id
            )
            reason_map = {
                SupervisorDecision.WAIT_PARTS: ProcessReason.WAITING_PARTS,
                SupervisorDecision.WAIT_OWNER: ProcessReason.WAITING_OWNER,
                SupervisorDecision.EXTERNAL_COORDINATION: (
                    ProcessReason.EXTERNAL_COORDINATION
                ),
                SupervisorDecision.CONTINUE: ProcessReason.NONE,
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
                reason_detail=cleaned_reason,
            )
            if code is SupervisorDecision.CONTINUE:
                self._resume_receipted_process(record, now=self._clock())
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
        if code is SupervisorDecision.REASSIGN:
            recipients: Iterable[int | None] = (record.reporter_user_id,)
            title = "工单待重新安排"
            body = "主管已将工单转回待派单队列"
        else:
            recipients = (record.reporter_user_id, active_assignee_user_id)
            title = "工单处理方案已更新"
            body = "主管已更新工单处理方案，请查看详情"
        self._notify_users(
            record,
            user_ids=recipients,
            exclude_user_ids=(current.user_id,),
            title=title,
            body=body,
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
        reset_response_sla_deadline(
            self._repository,
            work_order=record,
            now=now,
        )
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
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单已接单",
            body="工作人员已接单，请留意后续进度",
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
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单待重新安排",
            body="原处理人员无法接单，系统正在重新安排",
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
        self._notify_users(
            record,
            user_ids=(record.reporter_user_id,),
            title="工单开始处理",
            body="工作人员已开始处理工单",
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
        current_reason = ProcessReason(record.process_reason)
        target_reason = current_reason if process_reason is None else self._parse_enum(
            process_reason, ProcessReason, ProcessReason.NONE, "process_reason",
        )
        if target_reason != current_reason:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Use escalation or a supervisor decision to change the processing reason",
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
        process_id: int | None = None
        if file_ids:
            process = self._repository.get_current_process(record.id)
            if process is None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Processing cycle has not started",
                    status_code=409,
                )
            process_id = process.id
        self._attach_media(
            record,
            actor=current,
            file_ids=file_ids,
            taken_phase=phase,
            process_id=process_id,
        )
        self._apply_transition(
            record,
            event=WorkOrderEvent.PROCESS_UPDATE,
            to_status=WorkOrderStatus.PROCESSING,
            actor=current,
            actor_type=self._actor_type(current, record),
            request_id=request_id,
            process_reason=target_reason,
            reason_code=target_reason.value,
            reason_detail=cleaned,
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
        before_file_ids: tuple[int, ...],
        after_file_ids: tuple[int, ...],
        version: int | None,
        handling_mode: str | None = None,
        handling_note: str | None = None,
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
        from app.domain.receipt_handling import handling_reason

        # A supervisor may submit on behalf of the assignee. SELF refers to
        # the actual submitter's employee identity, never the assigned worker.
        participant_id = (self._require_actor_employee(current).id
                          if handling_mode in {'SELF', 'ASSISTED'} else None)
        declaration = None if handling_mode is None else dict(
            process_id=process.id, mode=handling_mode, note=handling_note,
            reported_participant_employee_id=participant_id,
        )
        now = self._clock()
        if resolved:
            if not staff_may_submit_resolved_receipt(record.process_reason):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Unresolved process reason cannot be completed",
                    status_code=409,
                )
            receipt = self._repository.add_receipt(
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
            self._attach_media(
                record,
                actor=current,
                file_ids=before_file_ids,
                taken_phase=MediaTakenPhase.BEFORE,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=after_file_ids,
                taken_phase=MediaTakenPhase.AFTER,
                process_id=process.id,
                receipt_id=receipt.id,
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
                reason_detail=handling_reason(handling_mode, handling_note),
                receipt_handling=None if declaration is None else dict(declaration, receipt_id=receipt.id),
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
            self._stop_sla(record)
        else:
            if unresolved_reason is None or not unresolved_reason.strip():
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="unresolved_reason is required",
                    status_code=422,
                )
            target_reason = ProcessReason.INTERNAL_UNRESOLVED
            receipt = self._repository.add_receipt(
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
            self._attach_media(
                record,
                actor=current,
                file_ids=before_file_ids,
                taken_phase=MediaTakenPhase.BEFORE,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            self._attach_media(
                record,
                actor=current,
                file_ids=after_file_ids,
                taken_phase=MediaTakenPhase.AFTER,
                process_id=process.id,
                receipt_id=receipt.id,
            )
            process.ended_at = now
            self._apply_transition(
                record,
                event=WorkOrderEvent.RECEIPT_UNRESOLVED,
                to_status=WorkOrderStatus.PROCESSING,
                actor=current,
                actor_type=self._actor_type(current, record),
                request_id=request_id,
                process_reason=target_reason,
                reason_code=target_reason.value,
                reason_detail=handling_reason(handling_mode, handling_note),
                receipt_handling=None if declaration is None else dict(declaration, receipt_id=receipt.id),
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
            self._notify_users(
                record,
                user_ids=(
                    record.reporter_user_id,
                    *self._repository.list_sla_management_user_ids(
                        tenant_id=record.tenant_id,
                        property_id=record.property_id,
                    ),
                ),
                exclude_user_ids=(current.user_id,),
                title="工单异常待处理",
                body="工单未解决并已上报，请查看详情",
            )
        return record

    def _append_receipt_media(
        self,
        *,
        actor: Principal,
        work_order_id: int,
        receipt_id: int,
        request_id: str,
        before_file_ids: tuple[int, ...],
        after_file_ids: tuple[int, ...],
        version: int | None,
    ) -> WorkOrder:
        current, record, _assignment, _employee = self._lock_assignee_action(
            actor, work_order_id, version, takeover=False
        )
        RbacService.require_permission(
            current,
            WORK_ORDER_RESOLVE,
            self._property_scope(record),
        )
        if record.status != WorkOrderStatus.WAIT_CONFIRM.value:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Receipt media can only be supplemented while awaiting confirmation",
                status_code=409,
            )
        process = self._repository.get_current_process(record.id)
        receipt = (
            None
            if process is None
            else self._repository.get_receipt_for_process(process.id)
        )
        if receipt is None or receipt.id != receipt_id:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="Receipt is not current for this processing cycle",
                status_code=409,
            )
        self._attach_media(
            record,
            actor=current,
            file_ids=before_file_ids,
            taken_phase=MediaTakenPhase.BEFORE,
            process_id=process.id,
            receipt_id=receipt.id,
        )
        self._attach_media(
            record,
            actor=current,
            file_ids=after_file_ids,
            taken_phase=MediaTakenPhase.AFTER,
            process_id=process.id,
            receipt_id=receipt.id,
        )
        record.version += 1
        self._write_log(
            record,
            actor=current,
            actor_type=WorkOrderActorType.STAFF,
            from_status=WorkOrderStatus.WAIT_CONFIRM,
            to_status=WorkOrderStatus.WAIT_CONFIRM,
            event=WorkOrderEvent.RECEIPT_MEDIA_APPENDED,
            request_id=request_id,
            reason_code="RECEIPT_MEDIA_APPENDED",
        )
        self._append_audit(
            action=AuditAction.WORK_ORDER_RECEIPT_MEDIA_APPENDED,
            resource_type=AuditResourceType.WORK_ORDER,
            actor_user_id=current.user_id,
            resource_id=record.id,
            request_id=request_id,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "receipt_id": receipt.id,
                "process_id": process.id,
                "before_count": len(before_file_ids),
                "after_count": len(after_file_ids),
            },
        )
        self._session.flush()
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
        if target not in _STAFF_ESCALATION_REASONS:
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
            reason_code=target.value,
            reason_detail=reason,
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
        self._notify_users(
            record,
            user_ids=(
                record.reporter_user_id,
                *self._repository.list_sla_management_user_ids(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
            ),
            exclude_user_ids=(current.user_id,),
            title="工单异常升级",
            body="工单已上报异常，请查看处理进度",
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
        RbacService.require_permission(
            current,
            WORK_ORDER_PROCESS,
            self._property_scope(record),
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
        self._assert_version(record, version)
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
        reason_detail: str | None = None,
        receipt_handling: dict[str, object] | None = None,
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
            reason_detail=reason_detail,
            receipt_handling=receipt_handling,
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
        reason_detail: str | None = None,
        receipt_handling: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {"event_code": event.value}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if reason_detail is not None:
            payload["reason"] = reason_detail
        if receipt_handling is not None:
            payload['receipt_handling_v1'] = dict(receipt_handling)
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
        from app.service.incident import IncidentCapacityService

        release_load = IncidentCapacityService(
            self._session,
            work_order_repository=self._repository,
        ).should_release_load(
            work_order=record,
            assignment=assignment,
        )
        if employee is not None and release_load:
            self._adjust_load(record, employee, delta=-1)
        record.current_assignment_id = None
        self._session.flush()

    def _end_current_process(self, record: WorkOrder, *, now: datetime) -> None:
        process = self._repository.get_current_process(record.id)
        if process is None or process.ended_at is not None:
            return
        process.ended_at = now
        self._session.flush()

    def _resume_receipted_process(self, record: WorkOrder, *, now: datetime) -> None:
        process = self._repository.get_current_process(record.id)
        if process is None or self._repository.get_receipt_for_process(process.id) is None:
            return
        assignment = self._repository.get_active_assignment_for_update(record.id)
        if assignment is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_TRANSITION,
                message="An active assignment is required to continue processing",
                status_code=409,
            )
        if process.ended_at is None:
            process.ended_at = now
        self._repository.add_process(
            WorkOrderProcess(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                cycle_no=process.cycle_no + 1,
                assignee_id=assignment.employee_id,
                started_at=now,
            )
        )
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

    def _manual_requirements(self, record: WorkOrder) -> tuple[str | None, str | None] | None:
        category = WorkOrderCategory(getattr(record, "category", "OTHER"))
        required_duty = None if category is WorkOrderCategory.OTHER else category_duty(category)
        required_skill = None if category is WorkOrderCategory.OTHER else category.value
        analysis = self._analysis.get_current_analysis(record.id)
        if analysis is not None and analysis.status == "COMPLETED":
            try:
                output = WorkOrderAnalysisOutput.model_validate({
                    field: getattr(analysis, field, None)
                    for field in WorkOrderAnalysisOutput.model_fields
                })
                mapped = map_analysis_output(output, threshold=self._settings.ai_confidence_threshold)
            except ValueError:
                # A purportedly completed but malformed requirement must not
                # silently discard a restriction and permit assignment.
                return None
            if mapped.category == category and not mapped.low_confidence:
                required_duty, required_skill = mapped.required_duty, mapped.required_skill
        return required_duty, required_skill

    def _eligibility_error(
        self,
        record: WorkOrder,
        employee: Employee,
        *,
        ignore_capacity: bool = False,
    ) -> AppError | None:
        # PA-101-A: one shared 409 body used to hide every denial cause, so a
        # rejected manual dispatch left the operator with no way to tell a skill
        # gate from attendance or capacity. The code and status stay the same;
        # only the machine-readable reason is now filled in.
        required_duty: str | None = None
        required_skill: str | None = None

        def denied(reason: str) -> AppError:
            return eligibility_denied(
                reason=reason,
                required_duty=required_duty,
                required_skill=required_skill,
            )

        if employee.status != EmployeeStatus.ACTIVE.value:
            return denied(FilterReason.NOT_ACTIVE.value)
        # PA-127 F1: `_assign` rejects a foreign employee before calling here,
        # so this branch is only reachable from callers that load the assignee
        # themselves (the resident reopen path at :1776). Kept as defence in
        # depth; the manual path now reports TENANT_MISMATCH from the
        # pre-check instead.
        if employee.tenant_id != record.tenant_id:
            return denied("TENANT_MISMATCH")
        if employee.user_id is None or self._analysis.get_user_status(employee.user_id) != "ACTIVE":
            return denied(FilterReason.USER_NOT_ACTIVE.value)
        roles = self._analysis.list_employee_role_codes(
            employee.user_id, tenant_id=record.tenant_id, property_id=record.property_id,
        )
        if not roles & DISPATCH_STAFF_ROLES:
            return denied(FilterReason.NO_STAFF_ROLE.value)
        now = self._clock()
        assignment = self._employees.find_effective_assignment(
            employee_id=employee.id,
            property_id=record.property_id,
            now=now,
        )
        if assignment is None:
            return denied(FilterReason.PROPERTY_NOT_EFFECTIVE.value)
        property_record = self._repository.get_property(record.property_id)
        if property_record is None:
            return denied(FilterReason.PROPERTY_NOT_EFFECTIVE.value)
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
            return denied(FilterReason.NOT_ON_DUTY.value)
        attendance = self._analysis.get_attendance(
            employee_id=employee.id,
            property_id=record.property_id,
            business_date=business_date,
        )
        if attendance is None or attendance.check_in_at is None:
            return denied(FilterReason.NOT_CHECKED_IN.value)
        requirements = self._manual_requirements(record)
        if requirements is None:
            return denied("ANALYSIS_UNREADABLE")
        required_duty, required_skill = requirements
        # Unknown classification keeps the explicitly retained manual fallback.
        if required_duty is not None:
            capability = capability_filter_reason(
                duty_codes=self._analysis.list_employee_duties(employee.id),
                skill_codes=self._analysis.list_employee_skills(employee.id),
                required_duty=required_duty, required_skill=required_skill,
            )
            if capability is not None:
                return denied(capability.value)
        if not ignore_capacity:
            load = self._repository.employee_global_occupancy(
                employee_id=employee.id, tenant_id=record.tenant_id, lock=True, collapse_incidents=True,
            )
            if not employee_is_capacity_available(current_load=load, max_capacity=employee.max_capacity):
                return denied(FilterReason.CAPACITY_FULL.value)
            if ordinary_maintenance(required_duty, required_skill) and load > 0:
                return denied(FilterReason.BUSY.value)
        return None

    def _attach_media(
        self,
        record: WorkOrder,
        *,
        actor: Principal,
        file_ids: tuple[int, ...],
        taken_phase: MediaTakenPhase,
        process_id: int | None = None,
        receipt_id: int | None = None,
    ) -> None:
        if not file_ids:
            return
        if taken_phase is MediaTakenPhase.REPORT:
            if process_id is not None or receipt_id is not None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="Report media cannot be linked to a processing cycle",
                    status_code=422,
                )
        elif taken_phase is MediaTakenPhase.ON_SITE:
            if process_id is None or receipt_id is not None:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="On-site media requires its processing cycle",
                    status_code=422,
                )
        elif process_id is None or receipt_id is None:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="Receipt media requires its process and receipt",
                status_code=422,
            )

        if process_id is not None:
            process = self._repository.get_current_process(record.id)
            if (
                process is None
                or process.id != process_id
                or process.work_order_id != record.id
                or process.tenant_id != record.tenant_id
                or process.property_id != record.property_id
            ):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Media process cycle is not current for this work order",
                    status_code=409,
                )
        if receipt_id is not None:
            receipt = self._repository.get_receipt_for_process(process_id)
            if (
                receipt is None
                or receipt.id != receipt_id
                or receipt.process_id != process_id
                or receipt.work_order_id != record.id
                or receipt.tenant_id != record.tenant_id
                or receipt.property_id != record.property_id
            ):
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_TRANSITION,
                    message="Media receipt does not belong to this processing cycle",
                    status_code=409,
                )
        seen: set[int] = set()
        for file_id in file_ids:
            self._require_positive_id(file_id, "file_ids")
            if file_id in seen:
                continue
            seen.add(file_id)
            file_object = self._repository.get_file(file_id)
            if (
                file_object is None
                or file_object.tenant_id != record.tenant_id
                or file_object.property_id not in {None, record.property_id}
                or file_object.uploader_user_id != actor.user_id
            ):
                raise AppError(
                    code=FileErrorCode.NOT_FOUND,
                    message="File was not found",
                    status_code=404,
                )
            if not self._file_is_visible(
                actor, file_object,
                audience="property" if taken_phase is MediaTakenPhase.ON_SITE else "resident",
            ):
                self._raise_file_not_found()
            kind = _ALLOWED_MIME.get(file_object.mime_type)
            if kind is None:
                raise AppError(
                    code=FileErrorCode.UNSUPPORTED_TYPE,
                    message="File type is not allowed",
                    status_code=422,
                )
            self._read_stored_file(file_object)
            self._repository.add_media(
                WorkOrderMedia(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    kind=kind.value,
                    file_id=file_object.id,
                    taken_phase=taken_phase.value,
                    process_id=process_id,
                    receipt_id=receipt_id,
                    uploader_user_id=actor.user_id,
                )
            )

    @staticmethod
    def _validate_audience(audience: str) -> None:
        if audience not in {"resident", "property"}:
            raise AppError(code=WorkOrderErrorCode.INVALID_INPUT, message="Invalid audience", status_code=422)

    def _authorize_read(self, actor: Principal, record: WorkOrder, *, allow_reporter: bool = True) -> None:
        if allow_reporter and record.reporter_user_id == actor.user_id:
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
                and employee.status == EmployeeStatus.ACTIVE.value
                and employee.tenant_id == record.tenant_id
                and self._employees.find_effective_assignment(
                    employee_id=employee.id,
                    property_id=record.property_id,
                    now=self._clock(),
                ) is not None
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

    def _resume_reopened_sla(self, record: WorkOrder) -> None:
        """Resume the original deadline without repeating prior SLA alerts."""
        clock = self._repository.get_sla_clock_for_update(record.id)
        if clock is None or clock.state != SlaClockState.STOPPED.value:
            return
        events = {item.event_code for item in self._repository.list_logs(record.id)}
        if WorkOrderEvent.SLA_OVERDUE.value in events:
            clock.state = SlaClockState.OVERDUE.value
        elif WorkOrderEvent.SLA_WARNING.value in events:
            clock.state = SlaClockState.WARNING.value
        else:
            clock.state = SlaClockState.OPEN.value

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

    def _notify_users(
        self,
        record: WorkOrder,
        *,
        user_ids: Iterable[int | None],
        title: str,
        body: str,
        exclude_user_ids: Iterable[int] = (),
    ) -> None:
        excluded = {int(user_id) for user_id in exclude_user_ids}
        recipients = {
            int(user_id)
            for user_id in user_ids
            if user_id is not None and int(user_id) not in excluded
        }
        for user_id in sorted(recipients):
            self._notify(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                user_id=user_id,
                title=title,
                body=body,
                resource_type="WORK_ORDER",
                resource_id=record.id,
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

    def _execute(self, operation: Callable[[], T], *, composing: bool = False, analysis_request_id: str | None = None) -> T:
        from app.core.database import run_composing_transaction, run_owned_transaction

        try:
            runner = run_composing_transaction if composing else run_owned_transaction
            def with_intent():
                result = operation()
                if (analysis_request_id is not None and self._settings.durable_tasks_enabled
                        and self._settings.should_enqueue_ai_on_create):
                    record = result[0] if isinstance(result, tuple) else result
                    created = result[1] if isinstance(result, tuple) else True
                    if created and record.status in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}:
                        from app.service.task_intent import ANALYZE, register_intent
                        register_intent(self._session, operation=ANALYZE, resource=record,
                                        request_id=analysis_request_id)
                return result
            return runner(self._session, with_intent)
        except AppError:
            raise
        except IntegrityError as exc:
            self._map_insert_error(exc)
            raise
        except OperationalError as exc:
            if retryable_mysql_transaction_error_code(exc) is not None:
                raise AppError(
                    code=WorkOrderErrorCode.CONFLICT,
                    message="Work order is busy; retry the operation",
                    status_code=409,
                    details={"retryable": True},
                ) from exc
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Work order operation could not be completed",
                status_code=500,
            ) from exc
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

    def _sla_versioning_available(self) -> bool:
        try:
            bind = self._session.get_bind()
            inspector = inspect(bind)
            return (
                inspector.has_table("work_order_sla_applications") is True
                and inspector.has_table("sla_policy_versions") is True
            )
        except (AttributeError, SQLAlchemyError):
            return False

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
        contact_phone: str | None = None,
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
                contact_phone or "",
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

    @staticmethod
    def _raise_file_not_found() -> None:
        raise AppError(
            code=FileErrorCode.NOT_FOUND,
            message="File was not found",
            status_code=404,
        )
