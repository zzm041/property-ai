"""Nine-field payload validation and the observation/skill contracts.

Moved verbatim out of app/service/analysis.py (package restructure phase 1).
"""
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
from app.domain.governance import AuditAction, AuditResourceType
from app.domain.ai import (
    ObservationBindingError,
    ObservedAnalysisOutput,
    bind_observations,
    build_evidence_catalog,
    prompt_evidence_catalog,
)
from app.domain.work_order import AttendanceOnDuty, EmployeeStatus
from app.domain.identity import OrganizationScope, Principal, ScopeType
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
from app.service.work_order.analysis_attempt import (
    accepts_attempt,
    claim_attempt,
    media_checkpoint_payload,
    owns_attempt,
    provider_checkpoint_payload,
    read_media_checkpoint,
    read_provider_checkpoint,
)
from app.service.task_intent import IntentLeaseLost, require_execution_lease
from app.service.identity.auth import AuthService
from app.service.identity.rbac import RbacService

logger = get_logger(__name__)


from . import context as shared
from .context import AnalysisSnapshot, MediaRef, ProviderBundle

class ValidationMixin:
    def _validate_payload(
        self,
        payload: Mapping[str, object] | None,
        *, evidence_catalog: Mapping[str, Mapping[str, object]] | None = None,
        request_id: str | None = None,
    ) -> tuple[MappedAnalysis | None, str | None]:
        if payload is None:
            return None, None
        try:
            trace=None
            if 'observations' in payload:
                observed=ObservedAnalysisOutput.model_validate(payload)
                key,trace=shared.bind_observations(observed,evidence_catalog or {})
                effective_labels = {item['label'] for item in trace['effective_observations']}
                if ('人员行为观察' in effective_labels
                        and (observed.category != 'security'
                             or effective_labels - {'人员行为观察', '敲门按铃', '噪声'}
                             or any(item['label'] == '噪声' and item['subject'] != '人员'
                                    for item in trace['effective_observations']))):
                    raise ObservationBindingError('Observation conflicts with duty category',
                                                  reason_code='duty_observation_conflict')
                if (effective_labels & {'火花烧损', '冒烟'} and observed.risk not in {'electric', 'gas', 'safety'}):
                    raise ObservationBindingError('Fire observation conflicts with risk category',
                                                  reason_code='risk_observation_conflict')
                if (any(item['label'] == '疑似燃气气味' for item in trace['effective_observations'])
                        and observed.risk not in {'gas', 'safety'}):
                    raise ObservationBindingError('Gas observation conflicts with risk category',
                                                  reason_code='risk_observation_conflict')
                trace['raw_model_output']=dict(payload)
                output=WorkOrderAnalysisOutput.model_validate({**observed.model_dump(exclude={'observations'}),'subcategory':key})
            else:
                if self._settings.llm_provider!='development':raise ValueError('V10 observable evidence required')
                output = WorkOrderAnalysisOutput.model_validate(payload)
            mapped = map_analysis_output(
                output,
                threshold=self._settings.ai_confidence_threshold,
            )
            if trace is not None:
                evidence_missing = not trace['effective_observations']
                if evidence_missing and not trace.get('manual_review_reason'):
                    trace['clarification_reason'] = 'NO_CONFIRMED_OBSERVATION'
                mapped = replace(mapped, event_trace=trace,
                    low_confidence=mapped.low_confidence or evidence_missing,
                    dispatch_fields_applied=mapped.dispatch_fields_applied and not evidence_missing)
            if mapped.skill_resolution == SKILL_RESOLUTION_UNMAPPED:
                # PA-167 explicit fallback: an unknown skill stays a hard
                # restriction (erasing it would open the dispatch gate), but it is
                # announced here and in the analysis audit, never silently carried
                # through as if it were a known code.
                logger.warning(
                    'analysis_skill_not_normalized',
                    request_id=request_id,
                    raw_required_skill=mapped.raw_required_skill,
                    required_skill=mapped.required_skill,
                    reason_code=SKILL_VALUE_NOT_NORMALIZED,
                )
        except (ValidationError, ValueError) as exc:
            if request_id is not None:
                reason = (exc.reason_code if isinstance(exc, ObservationBindingError)
                          else 'schema_invalid' if isinstance(exc, ValidationError)
                          else 'contract_invalid')
                logger.warning('analysis_payload_rejected', request_id=request_id,
                               error_code=AiErrorCode.INVALID_OUTPUT, reason_code=reason)
            return None, AiErrorCode.INVALID_OUTPUT
        return mapped, None

    @staticmethod
    @staticmethod
    def _fill_analysis(analysis: WorkOrderAnalysis, mapped: MappedAnalysis) -> None:
        analysis.category = mapped.category.value
        analysis.subcategory = mapped.subcategory
        analysis.severity = mapped.severity.value
        analysis.priority = mapped.priority.value
        analysis.summary = mapped.summary
        analysis.risk = mapped.risk.value
        analysis.required_role = mapped.required_role
        # PA-167: store the dispatch code, not the model's trade label. The row is
        # what the supervisor console prints and what re-dispatch starts from, so
        # it has to use the same vocabulary the dispatcher compares against. The
        # original label stays in the audit event (`raw_required_skill`) and in
        # MappedAnalysis.raw_required_skill, so provenance is not lost.
        analysis.required_skill = mapped.required_skill
        analysis.confidence = Decimal(str(mapped.confidence))


def _status_for_error(error: ProviderError) -> AiCallStatus:
    if isinstance(error, ProviderTimeoutError):
        return AiCallStatus.TIMEOUT
    if isinstance(error, ProviderInvalidOutputError):
        return AiCallStatus.INVALID
    if isinstance(error, (ProviderUnavailableError, ProviderRateLimitError)):
        return AiCallStatus.FAILED
    return AiCallStatus.FAILED
