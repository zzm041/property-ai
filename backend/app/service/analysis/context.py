"""Shared, test-visible surface of the analysis package.

The service used to import these straight into app/service/analysis.py; keeping
them in one module preserves the single patch point every test relies on, so
the restructure does not have to touch a single assertion.
"""
from app.core.database import begin_owned_transaction
from app.core.logging import get_logger
from app.core.time import utc_now
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.observed_event import bind_observations
from app.service.analysis_attempt import owns_attempt
from app.service.task_intent import require_execution_lease


MEDIA_CHECKPOINT_PROTOCOL_VERSION = "wo-media-checkpoint-v2"

# Keep recommendation eligibility aligned with docs/05: STAFF or a higher
# scoped property-management role, in addition to all live employee filters.
_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})



from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
import json

from pydantic import ValidationError
from sqlalchemy import MetaData, Table, inspect as sa_inspect, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import (
    begin_owned_transaction,
    require_owned_transaction,
    retryable_mysql_analysis_error_code,
)
from app.core.errors import (
    AiErrorCode,
    AppError,
    ErrorCode,
    WorkOrderErrorCode,
)
from app.core.logging import get_logger
from app.core.time import current_business_date, utc_now
from app.domain.ai import (
    AI_ANALYSIS_READ,
    AI_ANALYSIS_REVIEW,
    DISPATCH_RECOMMENDATION_READ,
    WORK_ORDER_ANALYSIS_PROMPT_VERSION,
    WORK_ORDER_SUMMARY_INSTRUCTION,
    AiCallStatus,
    AnalysisStatus,
    DispatchCandidate,
    FilterReason,
    SKILL_RESOLUTION_UNMAPPED,
    SKILL_VALUE_NOT_NORMALIZED,
    capability_note,
    MappedAnalysis,
    ProviderType,
    WorkOrderAnalysisOutput,
    hard_filter_candidate,
    map_analysis_output,
    rank_candidates,
)
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.observed_event import (
    ObservationBindingError,
    ObservedAnalysisOutput,
    bind_observations,
    build_evidence_catalog,
    prompt_evidence_catalog,
)
from app.domain.employee import AttendanceOnDuty, EmployeeStatus
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.work_order import (
    MediaKind,
    WorkOrderActorType,
    WorkOrderEvent,
    WorkOrderStatus,
    require_legal_transition,
)
from app.model.ai import (
    AiCallLog,
    DispatchRecommendation,
    WorkOrderAnalysis,
)
from app.model.work_order import WorkOrder, WorkOrderLog
from app.provider.asr import ASRProvider
from app.provider.errors import (
    ProviderError,
    ProviderInvalidOutputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.factory import (
    build_asr_provider,
    build_llm_provider,
    build_vlm_provider,
)
from app.provider.llm import LLMProvider
from app.provider.vision import VLMProvider
from app.provider.usage import valid_token_count
from app.repository.ai import AiRepository
from app.repository.identity import IdentityRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.analysis_attempt import (
    accepts_attempt,
    claim_attempt,
    media_checkpoint_payload,
    owns_attempt,
    provider_checkpoint_payload,
    read_media_checkpoint,
    read_provider_checkpoint,
)
from app.service.task_intent import IntentLeaseLost, require_execution_lease
from app.service.auth import AuthService
from app.service.rbac import RbacService

# PA-169: the two provider answers that mean "the model produced output the
# contract cannot use". They are the only failures that may quarantine an
# input hash; runtime failures must stay retryable.
QUARANTINE_ERROR_CODES = (AiErrorCode.INVALID_OUTPUT, AiErrorCode.OUTPUT_UNVERIFIABLE)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MediaRef:
    storage_key: str
    mime_type: str
    kind: str
    media_id: int | None = None
    file_id: int | None = None
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    work_order_id: int
    tenant_id: int
    property_id: int
    house_id: int
    building_id: int | None
    analysis_id: int
    analysis_version: int
    work_order_version: int
    input_hash: str
    text: str
    existing_asr_text: str | None
    images: tuple[MediaRef, ...]
    audio: tuple[MediaRef, ...]
    context: Mapping[str, object] = field(default_factory=dict)
    attachment_completion: bool = False
    source_analysis_id: int | None = None
    source_analysis_version: int | None = None
    source_input_hash: str | None = None
    original_input_hash: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    asr_text: str | None
    vision_text: str | None
    llm_payload: Mapping[str, object] | None
    error_code: str | None
    call_status: str
    # PA-169/F3: a resource refusal carries the numbers it was refused on
    # (resource_floor.short_by_mib). Registered here because both the orchestrator
    # and the persistence mixin read it.
    resource_floor: Mapping[str, object] | None = None



logger = get_logger("app.service.analysis")
