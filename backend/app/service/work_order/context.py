"""Shared imports, constants and module helpers for the work_order service package.

S6 moved the 3876-line `app.service.work_order` module here verbatim. Every name that the
mixin modules reference at module level lives in this file exactly once, so patching
`app.service.work_order.context.<name>` reaches every mixin.
"""
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
from .media_scan import admit_scanned_media, log_media_scan_skipped
from app.core.time import current_business_date, utc_now
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.ai import FilterReason, WorkOrderAnalysisOutput, capability_filter_reason, category_duty, content_fingerprint, eligibility_denial_details, loose_content_fingerprint, map_analysis_output, ordinary_maintenance
from app.domain.work_order import DISPATCH_STAFF_ROLES, AttendanceOnDuty, EmployeeStatus
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.domain.work_order import resident_media_visible
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
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService
from .sla import (
    reset_assignment_sla_deadlines,
    reset_response_sla_deadline,
)

T = TypeVar("T")
logger = get_logger(__name__)

# PA-169 (t150): how long a repeated report counts as the same submission. Only
# the same resident on the same house is merged; 10 minutes is the window the
# guard was specified with.
DUPLICATE_WINDOW_MINUTES = 10
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


