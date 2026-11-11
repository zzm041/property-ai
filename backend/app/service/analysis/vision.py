"""Vision and speech provider calls (image inspection, ASR transcription).

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

from .validation import _status_for_error

class VisionMixin:
    def _run_vlm(
        self,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        descriptions: list[dict[str, object]] = []
        first_error = None
        for index, media in enumerate(snapshot.images, start=1):
            description = self._load_media_checkpoint(snapshot, "VLM", media)
            source = "checkpoint"
            error = None
            if description is None:
                source = "provider"
                description, error = self._inspect_image(snapshot, media, request_id, attempt)
                if description is not None and error is None:
                    self._save_media_checkpoint(
                        snapshot,
                        "VLM",
                        media,
                        description,
                        request_id,
                    )
            descriptions.append({
                "image_index": index,
                "description": description,
                "error_code": error,
                "source": source,
            })
            first_error = first_error or error
        if not any(item["description"] for item in descriptions):
            return None, first_error
        return json.dumps(descriptions, ensure_ascii=False), first_error

    def _inspect_image(
        self, snapshot: AnalysisSnapshot, media: MediaRef, request_id: str, attempt: int
    ) -> tuple[str | None, str | None]:
        image_hash = sha256(
            f"{shared.MEDIA_CHECKPOINT_PROTOCOL_VERSION}|{media.kind}|{media.mime_type}|"
            f"{media.file_id}|{media.content_hash}".encode("utf-8")
        ).hexdigest()
        started = perf_counter()
        started_at = self._clock()
        try:
            insight = self._vlm.inspect_image(
                storage_key=media.storage_key,
                mime_type=media.mime_type,
            )
            if not isinstance(insight.description, str) or not insight.description.strip():
                raise ProviderInvalidOutputError("VLM returned an empty description")
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.VLM,
                operation="inspect_image",
                status=AiCallStatus.SUCCESS,
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=insight.model_version,
                token_usage=getattr(insight, "token_usage", None),
                input_hash=image_hash,
            )
            return insight.description, None
        except ProviderError as exc:
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.VLM,
                operation="inspect_image",
                status=_status_for_error(exc),
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=getattr(self._vlm, "model_version", "unknown"),
                error_code=exc.error_code,
                input_hash=image_hash,
            )
            return None, exc.error_code

    def _run_asr(
        self,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        return self._transcribe_audio(snapshot, snapshot.audio[0], request_id, attempt)

    def _transcribe_audio(
        self,
        snapshot: AnalysisSnapshot,
        media: MediaRef,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        audio_hash = sha256(
            f"{shared.MEDIA_CHECKPOINT_PROTOCOL_VERSION}|{media.kind}|{media.mime_type}|"
            f"{media.file_id}|{media.content_hash}".encode("utf-8")
        ).hexdigest()
        started = perf_counter()
        started_at = self._clock()
        try:
            transcript = self._asr.transcribe(
                storage_key=media.storage_key,
                mime_type=media.mime_type,
            )
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.ASR,
                operation="transcribe",
                status=AiCallStatus.SUCCESS,
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=transcript.model_version,
                token_usage=getattr(transcript, "token_usage", None),
                input_hash=audio_hash,
            )
            return transcript.text, None
        except ProviderError as exc:
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.ASR,
                operation="transcribe",
                status=_status_for_error(exc),
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=getattr(self._asr, "model_version", "unknown"),
                error_code=exc.error_code,
                input_hash=audio_hash,
            )
            return None, exc.error_code

    def _run_all_asr(
        self,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        transcripts: list[dict[str, object]] = []
        first_error = None
        for index, media in enumerate(snapshot.audio, start=1):
            text = self._load_media_checkpoint(snapshot, "ASR", media)
            source = "checkpoint"
            error = None
            if text is None:
                text = self._load_legacy_first_audio_checkpoint(snapshot, media)
                if text is not None:
                    source = "legacy_checkpoint"
                    self._save_media_checkpoint(
                        snapshot,
                        "ASR",
                        media,
                        text,
                        request_id,
                        source=source,
                    )
            if text is None:
                source = "provider"
                text, error = self._transcribe_audio(
                    snapshot,
                    media,
                    request_id,
                    attempt,
                )
                if text is not None and error is None:
                    self._save_media_checkpoint(
                        snapshot,
                        "ASR",
                        media,
                        text,
                        request_id,
                    )
            transcripts.append({
                "audio_index": index,
                "text": text,
                "error_code": error,
                "source": source,
            })
            first_error = first_error or error
        if not any(item["text"] for item in transcripts):
            return None, first_error
        return json.dumps(transcripts, ensure_ascii=False), first_error

