"""Persistence: call logs, attempts, analyses, recommendations and audits.

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

logger = get_logger(__name__)


from . import context as shared
from .context import AnalysisSnapshot, MediaRef, ProviderBundle

class PersistenceMixin:
    def _provider_model_version(self, provider_type: str) -> str:
        provider = self._asr if provider_type == "ASR" else self._vlm
        return str(getattr(provider, "model_version", "unknown"))

    def _provider_checkpoint_contract(self, provider_type: str) -> str | None:
        if provider_type not in {"ASR", "VLM"}:
            return None
        provider = self._asr if provider_type == "ASR" else self._vlm
        fingerprint = getattr(provider, "checkpoint_fingerprint", None)
        if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)):
            return None
        return fingerprint

    def _media_checkpoint_protocol(self, provider_type: str) -> str | None:
        fingerprint = self._provider_checkpoint_contract(provider_type)
        if fingerprint is None:
            return None
        return f"{shared.MEDIA_CHECKPOINT_PROTOCOL_VERSION}:{provider_type.lower()}-contract-v1:{fingerprint}"

    def _load_provider_checkpoint(
        self,
        snapshot: AnalysisSnapshot,
        provider_type: str,
    ) -> str | None:
        context = getattr(self, "_task_context", None)
        if not context:
            return None
        contract = self._provider_checkpoint_contract(provider_type)
        if contract is None:
            return None
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
            shared.require_execution_lease(self._session)
            analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
            if record is None or not shared.owns_attempt(self._work_orders, record, analysis, context):
                raise IntentLeaseLost()
            checkpoint = self._work_orders.latest_analysis_provider_checkpoint(
                snapshot.work_order_id,
                provider_type,
            )
            if checkpoint is None:
                return None
            if (not isinstance(checkpoint.payload_json, dict)
                    or checkpoint.payload_json.get("provider_contract") != contract):
                return None
            return read_provider_checkpoint(
                checkpoint.payload_json,
                snapshot,
                context,
                provider_type,
                self._provider_model_version(provider_type),
            )

    def _save_provider_checkpoint(
        self,
        snapshot: AnalysisSnapshot,
        provider_type: str,
        value: str,
        request_id: str,
    ) -> None:
        context = getattr(self, "_task_context", None)
        if not context:
            return
        contract = self._provider_checkpoint_contract(provider_type)
        if contract is None:
            return
        model_version = self._provider_model_version(provider_type)
        payload = provider_checkpoint_payload(
            snapshot,
            context,
            provider_type,
            model_version,
            value,
        )
        payload["provider_contract"] = contract
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
            shared.require_execution_lease(self._session)
            analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
            if record is None or not shared.owns_attempt(self._work_orders, record, analysis, context):
                raise IntentLeaseLost()
            existing = self._work_orders.latest_analysis_provider_checkpoint(
                snapshot.work_order_id,
                provider_type,
            )
            if (existing is not None and isinstance(existing.payload_json, dict)
                    and existing.payload_json.get("provider_contract") == contract):
                reused = read_provider_checkpoint(
                    existing.payload_json,
                    snapshot,
                    context,
                    provider_type,
                    model_version,
                )
                if reused is not None:
                    return
            self._work_orders.add_log(WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type="SYSTEM",
                from_status=record.status,
                to_status=record.status,
                process_reason=record.process_reason,
                event_code=f"AI_{provider_type}_CHECKPOINT",
                request_id=request_id,
                payload_json=payload,
            ))

    def _load_media_checkpoint(
        self,
        snapshot: AnalysisSnapshot,
        provider_type: str,
        media: MediaRef,
    ) -> str | None:
        context = getattr(self, "_task_context", None)
        if not context:
            return None
        protocol_version = self._media_checkpoint_protocol(provider_type)
        if protocol_version is None:
            return None
        model_version = self._provider_model_version(provider_type)
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
            shared.require_execution_lease(self._session)
            analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
            if record is None or not shared.owns_attempt(self._work_orders, record, analysis, context):
                raise IntentLeaseLost()
            for checkpoint in self._work_orders.analysis_provider_checkpoints(
                snapshot.work_order_id,
                provider_type,
            ):
                value = read_media_checkpoint(
                    checkpoint.payload_json,
                    provider_type,
                    model_version,
                    protocol_version,
                    media,
                )
                if value is not None:
                    return value
        return None

    def _load_legacy_first_audio_checkpoint(
        self,
        snapshot: AnalysisSnapshot,
        media: MediaRef,
    ) -> str | None:
        """Upgrade a first-audio result only when its provider contract is proven."""
        contract = self._provider_checkpoint_contract("ASR")
        context = getattr(self, "_task_context", None)
        if (
            not context
            or contract is None
            or not snapshot.attachment_completion
            or not snapshot.audio
            or media != snapshot.audio[0]
            or snapshot.source_analysis_id is None
            or snapshot.source_analysis_version is None
            or snapshot.source_input_hash is None
            or snapshot.source_input_hash != snapshot.original_input_hash
            or not snapshot.existing_asr_text
        ):
            return None
        model_version = self._provider_model_version("ASR")
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
            shared.require_execution_lease(self._session)
            analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
            if record is None or not shared.owns_attempt(self._work_orders, record, analysis, context):
                raise IntentLeaseLost()
            for checkpoint in self._work_orders.analysis_provider_checkpoints(
                snapshot.work_order_id,
                "ASR",
            ):
                payload = checkpoint.payload_json
                if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                    continue
                expected = {
                    "analysis_id": snapshot.source_analysis_id,
                    "analysis_version": snapshot.source_analysis_version,
                    "input_hash": snapshot.source_input_hash,
                    "provider_type": "ASR",
                    "model_version": model_version,
                    "provider_contract": contract,
                }
                if any(payload.get(key) != value for key, value in expected.items()):
                    continue
                value = payload.get("value")
                digest = payload.get("value_sha256")
                if (
                    isinstance(value, str)
                    and value == snapshot.existing_asr_text
                    and isinstance(digest, str)
                    and sha256(value.encode("utf-8")).hexdigest() == digest
                ):
                    return value
        return None

    def _save_media_checkpoint(
        self,
        snapshot: AnalysisSnapshot,
        provider_type: str,
        media: MediaRef,
        value: str,
        request_id: str,
        *,
        source: str = "provider",
    ) -> None:
        context = getattr(self, "_task_context", None)
        if not context:
            return
        protocol_version = self._media_checkpoint_protocol(provider_type)
        if protocol_version is None:
            return
        model_version = self._provider_model_version(provider_type)
        payload = media_checkpoint_payload(
            snapshot,
            context,
            provider_type,
            model_version,
            protocol_version,
            media,
            value,
            source=source,
        )
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
            shared.require_execution_lease(self._session)
            analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
            if record is None or not shared.owns_attempt(self._work_orders, record, analysis, context):
                raise IntentLeaseLost()
            for checkpoint in self._work_orders.analysis_provider_checkpoints(
                snapshot.work_order_id,
                provider_type,
            ):
                if read_media_checkpoint(
                    checkpoint.payload_json,
                    provider_type,
                    model_version,
                    protocol_version,
                    media,
                ) is not None:
                    return
            self._work_orders.add_log(WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type="SYSTEM",
                from_status=record.status,
                to_status=record.status,
                process_reason=record.process_reason,
                event_code=f"AI_{provider_type}_CHECKPOINT",
                request_id=request_id,
                payload_json=payload,
            ))

    def _write_call_log(
        self,
        snapshot: AnalysisSnapshot,
        *,
        provider_type: ProviderType,
        operation: str,
        status: AiCallStatus,
        started_at: datetime,
        latency_ms: int,
        request_id: str,
        attempt: int,
        model_version: str,
        error_code: str | None = None,
        token_usage: int | None = None,
        input_hash: str | None = None,
    ) -> None:
        finished_at = started_at + timedelta(milliseconds=max(latency_ms, 0))
        with shared.begin_owned_transaction(self._session):
            call_log = self._ai.add_call_log(
                AiCallLog(
                    tenant_id=snapshot.tenant_id,
                    property_id=snapshot.property_id,
                    work_order_id=snapshot.work_order_id,
                    request_id=request_id,
                    provider_type=provider_type.value,
                    operation=operation,
                    biz_type="WORK_ORDER_ANALYSIS",
                    prompt_version=shared.WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                    model_version=model_version,
                    status=status.value,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    error_code=error_code,
                    token_usage=valid_token_count(token_usage),
                    input_hash=input_hash or snapshot.input_hash,
                )
            )
            self._write_provider_mode(call_log.id, provider_type)

    def _write_provider_mode(
        self,
        call_log_id: int,
        provider_type: ProviderType,
    ) -> None:
        """Populate the 0009 provenance column without mapping it on shared 0007."""

        bind = self._session.get_bind()
        if bind is None:
            return
        columns = {
            column["name"]
            for column in sa_inspect(bind).get_columns("ai_call_logs")
        }
        if "provider_mode" not in columns:
            return
        configured = {
            ProviderType.LLM: self._settings.llm_provider,
            ProviderType.VLM: self._settings.vlm_provider,
            ProviderType.ASR: self._settings.asr_provider,
        }[provider_type]
        table = Table("ai_call_logs", MetaData(), autoload_with=bind)
        self._session.execute(
            update(table)
            .where(table.c.id == call_log_id)
            .values(provider_mode=configured.upper())
        )

    def _apply(
        self,
        *,
        snapshot: AnalysisSnapshot,
        bundle: ProviderBundle,
        request_id: str,
        attempt: int,
    ) -> WorkOrderAnalysis | None:
        del attempt
        try:
            with shared.begin_owned_transaction(self._session):
                record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
                shared.require_execution_lease(self._session)
                analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
                if record is None or analysis is None:
                    return None
                if analysis.id != snapshot.analysis_id:
                    return None
                if not shared.owns_attempt(self._work_orders, record, analysis, getattr(self, "_task_context", None)):
                    return None
                if analysis.status != AnalysisStatus.PROCESSING.value:
                    return analysis
                # Keep all reads before the property serialization lock as
                # current/locking reads under MySQL REPEATABLE READ. Otherwise
                # this content lookup can establish a stale consistent snapshot
                # that later cluster/link reads retain after waiting on the lock.
                content = self._work_orders.get_content_for_update(record.id)
                if snapshot.attachment_completion:
                    stale = (
                        record.status != WorkOrderStatus.PENDING_ASSIGNMENT.value
                        or record.version != snapshot.work_order_version
                        or record.current_assignment_id is not None
                        or self._work_orders.get_active_assignment(record.id) is not None
                        or analysis.input_hash != snapshot.input_hash
                    )
                else:
                    stale = (
                        record.status != WorkOrderStatus.PENDING_ANALYSIS.value
                        or record.version != snapshot.work_order_version
                        or analysis.input_hash != snapshot.input_hash
                    )
                mapped, mapped_error = self._validate_payload(
                    bundle.llm_payload,
                    request_id=request_id,
                    evidence_catalog=build_evidence_catalog(
                        report_text=snapshot.text,
                        asr_text=bundle.asr_text,
                        vision_text=bundle.vision_text,
                    ),
                )
                if stale:
                    analysis.status = AnalysisStatus.STALE.value
                    analysis.error_code = AiErrorCode.STALE_RESULT
                    self._append_failure_audit(
                        record,
                        analysis,
                        request_id,
                        AiErrorCode.STALE_RESULT,
                    )
                    return analysis
                if content is not None and bundle.asr_text:
                    content.asr_text = bundle.asr_text
                if mapped is None:
                    analysis.status = AnalysisStatus.FAILED.value
                    analysis.error_code = mapped_error or bundle.error_code or AiErrorCode.PROVIDER_FAILED
                    if snapshot.attachment_completion:
                        self._work_orders.add_log(WorkOrderLog(
                            tenant_id=record.tenant_id,
                            property_id=record.property_id,
                            work_order_id=record.id,
                            actor_user_id=None,
                            actor_type=WorkOrderActorType.SYSTEM.value,
                            from_status=record.status,
                            to_status=record.status,
                            process_reason=record.process_reason,
                            event_code="AI_ATTACHMENT_INCOMPLETE",
                            request_id=request_id,
                            payload_json={
                                "source_analysis_id": snapshot.source_analysis_id,
                                "analysis_id": analysis.id,
                                "analysis_version": analysis.analysis_version,
                                "reason_code": analysis.error_code,
                            },
                        ))
                    self._append_failure_audit(
                        record,
                        analysis,
                        request_id,
                        analysis.error_code,
                    )
                    return analysis
                self._fill_analysis(analysis, mapped)
                if mapped.low_confidence:
                    analysis.status = AnalysisStatus.LOW_CONFIDENCE.value
                    analysis.error_code = (
                        AiErrorCode.OUT_OF_SCOPE
                        if (mapped.event_trace or {}).get('manual_review_reason') == 'OUT_OF_SCOPE_REQUEST'
                        else AiErrorCode.LOW_CONFIDENCE
                    )
                    if snapshot.attachment_completion:
                        self._work_orders.add_log(WorkOrderLog(
                            tenant_id=record.tenant_id,
                            property_id=record.property_id,
                            work_order_id=record.id,
                            actor_user_id=None,
                            actor_type=WorkOrderActorType.SYSTEM.value,
                            from_status=record.status,
                            to_status=record.status,
                            process_reason=record.process_reason,
                            event_code="AI_ATTACHMENT_INCOMPLETE",
                            request_id=request_id,
                            payload_json={
                                "source_analysis_id": snapshot.source_analysis_id,
                                "analysis_id": analysis.id,
                                "analysis_version": analysis.analysis_version,
                                "reason_code": analysis.error_code,
                            },
                        ))
                else:
                    analysis.status = AnalysisStatus.COMPLETED.value
                    analysis.error_code = None
                    record.category = mapped.category.value
                    record.severity = mapped.severity.value
                    record.priority = mapped.priority.value
                    if snapshot.attachment_completion:
                        record.version += 1
                        self._work_orders.add_log(WorkOrderLog(
                            tenant_id=record.tenant_id,
                            property_id=record.property_id,
                            work_order_id=record.id,
                            actor_user_id=None,
                            actor_type=WorkOrderActorType.SYSTEM.value,
                            from_status=record.status,
                            to_status=record.status,
                            process_reason=record.process_reason,
                            event_code="AI_ATTACHMENT_COMPLETED",
                            request_id=request_id,
                            payload_json={
                                "source_analysis_id": snapshot.source_analysis_id,
                                "analysis_id": analysis.id,
                                "analysis_version": analysis.analysis_version,
                                "attachment_count": len(snapshot.images) + len(snapshot.audio),
                            },
                        ))
                    else:
                        self._transition_analyzed(record, request_id)
                    from app.service.incident import IncidentService

                    IncidentService(self._session, settings=self._settings).aggregate_locked(
                        record,
                        subcategory=mapped.subcategory,
                        request_id=request_id,
                    )
                self._write_recommendations(record, analysis, mapped, snapshot.building_id)
                self._append_success_audit(record, analysis, request_id, mapped)
                return analysis
        except AppError:
            raise
        except OperationalError as exc:
            if retryable_mysql_analysis_error_code(exc) is not None:
                raise
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="AI analysis could not be stored",
                status_code=500,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="AI analysis could not be stored",
                status_code=500,
            ) from exc

    def record_database_exhaustion(self, *, work_order_id: int, request_id: str, context: dict) -> bool:
        """Fresh-session failure boundary. Never claim persistence if it raises."""
        with shared.begin_owned_transaction(self._session):
            record = self._work_orders.get_work_order_for_update(work_order_id)
            shared.require_execution_lease(self._session)
            if record is None or record.status not in {"PENDING_ANALYSIS", "PENDING_ASSIGNMENT"}:
                return False
            current = self._ai.get_current_analysis_for_update(work_order_id)
            latest = self._work_orders.latest_analysis_attempt(work_order_id)
            payload = latest.payload_json if latest is not None else None
            if payload:
                if payload["task_id"] != context["task_id"] or payload["attempt"] > context["attempt"]:
                    return False
                if payload["attempt"] == context["attempt"] and payload.get("execution_token") != context["execution_token"]:
                    return False
                expected = payload["work_order_version"]
                if record.status == "PENDING_ASSIGNMENT" and (
                    payload["from_status"] == "PENDING_ANALYSIS"
                    or (
                        payload.get("attachment_completion") is True
                        and current is not None
                        and current.status == AnalysisStatus.COMPLETED.value
                    )
                ):
                    expected += 1
                if record.version != expected or current is None or current.id != payload["analysis_id"] or current.input_hash != payload["input_hash"]:
                    return False
            elif context.get("expected_version") is None or record.version != context["expected_version"]:
                return False
            if record.current_assignment_id is not None or self._work_orders.get_active_assignment(record.id) is not None:
                return False
            code = AiErrorCode.DATABASE_RETRY_EXHAUSTED
            if current is not None and current.status == AnalysisStatus.PROCESSING.value:
                current.status = AnalysisStatus.FAILED.value
                current.error_code = code
            self._work_orders.add_log(WorkOrderLog(
                tenant_id=record.tenant_id, property_id=record.property_id,
                work_order_id=record.id, actor_user_id=None, actor_type="SYSTEM",
                from_status=record.status, to_status=record.status,
                process_reason=record.process_reason, event_code="AI_TASK_FAILED",
                request_id=request_id, payload_json={"error_code": code,
                    "task_id": context["task_id"], "attempt": context["attempt"],
                    "analysis_id": current.id if current is not None else None,
                    "work_order_version": record.version},
            ))
            if current is not None:
                self._append_failure_audit(record, current, request_id, code)
            return True

    def _transition_analyzed(self, record: WorkOrder, request_id: str) -> None:
        from_status = WorkOrderStatus(record.status)
        to_status = WorkOrderStatus.PENDING_ASSIGNMENT
        require_legal_transition(
            from_status=from_status,
            to_status=to_status,
            event=WorkOrderEvent.ANALYZE,
        )
        record.status = to_status.value
        record.version += 1
        self._work_orders.add_log(
            WorkOrderLog(
                tenant_id=record.tenant_id,
                property_id=record.property_id,
                work_order_id=record.id,
                actor_user_id=None,
                actor_type=WorkOrderActorType.SYSTEM.value,
                from_status=from_status.value,
                to_status=to_status.value,
                process_reason=record.process_reason,
                event_code=WorkOrderEvent.ANALYZE.value,
                request_id=request_id,
                payload_json={"event_code": WorkOrderEvent.ANALYZE.value, "reason_code": "AI_ANALYSIS"},
            )
        )

    def _write_recommendations(
        self,
        record: WorkOrder,
        analysis: WorkOrderAnalysis,
        mapped: MappedAnalysis,
        building_id: int | None,
    ) -> None:
        self._ai.delete_recommendations(analysis.id)
        # Low confidence is a valid clarification decision, not a dispatch
        # input. Preserve raw analysis/audit fields without selecting people.
        if mapped.low_confidence:
            return
        now = self._clock()
        property_record = self._work_orders.get_property(record.property_id)
        if property_record is None:
            return
        business_date = current_business_date(property_record.timezone, clock=self._clock)
        employees = self._ai.list_property_employees(
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        candidates: list[DispatchCandidate] = []
        ineligible: list[tuple[DispatchCandidate, FilterReason]] = []
        incident = self._ai.get_incident(record.incident_id) if record.incident_id else None
        primary_employee_id = None if incident is None else incident.primary_employee_id
        for employee in employees:
            assignment = self._ai.get_effective_property(
                employee_id=employee.id,
                property_id=record.property_id,
                now=now,
            )
            daily = self._ai.get_daily_status(
                employee_id=employee.id,
                property_id=record.property_id,
                business_date=business_date,
            )
            attendance = self._ai.get_attendance(
                employee_id=employee.id,
                property_id=record.property_id,
                business_date=business_date,
            )
            roles = (
                self._ai.list_employee_role_codes(
                    employee.user_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                )
                if employee.user_id
                else frozenset()
            )
            response, completion, rating = self._ai.get_dispatch_history(
                employee_id=employee.id,
                tenant_id=record.tenant_id,
                property_id=record.property_id,
            )
            candidate = DispatchCandidate(
                employee_id=employee.id,
                duty_codes=self._ai.list_employee_duties(employee.id),
                skill_codes=self._ai.list_employee_skills(employee.id),
                current_load=self._work_orders.employee_global_occupancy(
                    employee_id=employee.id, tenant_id=record.tenant_id,
                ) if mapped.required_duty == "MAINTENANCE" else (0 if daily is None else daily.current_load),
                last_assignment_id=self._work_orders.employee_last_assignment_id(
                    employee_id=employee.id, tenant_id=record.tenant_id,
                ),
                max_capacity=employee.max_capacity,
                on_duty=daily is not None
                and daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
                employee_active=employee.status == EmployeeStatus.ACTIVE.value,
                user_active=self._ai.get_user_status(employee.user_id) == "ACTIVE",
                property_effective=assignment is not None,
                has_staff_role=bool(roles & shared._STAFF_ROLES),
                checked_in=attendance is not None,
                check_in_at=None if attendance is None else attendance.check_in_at,
                same_building_history=self._ai.count_same_building_history(
                    employee_id=employee.id,
                    building_id=building_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
                primary_incident_handler=employee.id == primary_employee_id,
                incident_follow=(incident is not None and incident.primary_work_order_id != record.id
                                 and employee.id == primary_employee_id),
                average_response_seconds=response,
                completion_rate=completion,
                average_rating=rating,
            )
            reason = hard_filter_candidate(
                candidate,
                required_skill=mapped.required_skill,
                required_duty=mapped.required_duty,
            )
            if reason is None:
                candidates.append(candidate)
            else:
                ineligible.append((candidate, reason))
        from app.service.auto_dispatch import configured_dispatch_weights

        ranked = rank_candidates(
            tuple(candidates),
            required_skill=mapped.required_skill,
            required_duty=mapped.required_duty,
            weights=configured_dispatch_weights(self._settings),
        )
        rank_no = 1
        # PA-127 F2: record the capability gate's inputs and note on every
        # recommendation row, so an eligible `other` skill is auditable from the
        # recommendation table alone (reason_codes is a JSON column: no schema
        # change needed).
        gate_note = capability_note(
            required_duty=mapped.required_duty,
            required_skill=mapped.required_skill,
        )
        gate_codes = () if gate_note is None else (gate_note,)
        for candidate, score, reasons in ranked:
            self._ai.add_recommendation(
                DispatchRecommendation(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    analysis_id=analysis.id,
                    employee_id=candidate.employee_id,
                    score=Decimal(str(score)),
                    rank_no=rank_no,
                    eligible=1,
                    reason_codes=list(reasons) + list(gate_codes),
                    filter_reason=None,
                )
            )
            rank_no += 1
        for candidate, reason in ineligible:
            self._ai.add_recommendation(
                DispatchRecommendation(
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    work_order_id=record.id,
                    analysis_id=analysis.id,
                    employee_id=candidate.employee_id,
                    score=Decimal("0"),
                    rank_no=0,
                    eligible=0,
                    reason_codes=[reason.value, *gate_codes],
                    filter_reason=reason.value,
                )
            )

    def _append_success_audit(
        self,
        record: WorkOrder,
        analysis: WorkOrderAnalysis,
        request_id: str,
        mapped: MappedAnalysis,
    ) -> None:
        self._audit.append(
            action=AuditAction.WORK_ORDER_AI_ANALYSIS_COMPLETED,
            resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
            actor_user_id=None,
            resource_id=analysis.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "analysis_id": analysis.id,
                "status": analysis.status,
                "confidence": mapped.confidence,
                "dispatch_contract_version": mapped.dispatch_contract_version,
                "decision_code": (
                    "OUT_OF_SCOPE_REVIEW_REQUIRED"
                    if (mapped.event_trace or {}).get('manual_review_reason') == 'OUT_OF_SCOPE_REQUEST'
                    else "CLARIFICATION_REQUIRED"
                    if mapped.low_confidence
                    else "DISPATCH_FIELDS_ACCEPTED"
                ),
                "dispatch_fields_applied": mapped.dispatch_fields_applied,
                "raw_category": mapped.raw_category or mapped.category.value,
                "effective_category": (
                    None if mapped.low_confidence else mapped.category.value
                ),
                "raw_required_skill": mapped.raw_required_skill or mapped.required_skill,
                "effective_required_skill": (
                    None if mapped.low_confidence else mapped.required_skill
                ),
                "skill_mapping_applied": mapped.skill_mapping_applied,
                "skill_resolution": mapped.skill_resolution,
                **({'event_evidence':mapped.event_trace} if mapped.event_trace is not None else {}),
            },
        )

    def _append_failure_audit(
        self,
        record: WorkOrder,
        analysis: WorkOrderAnalysis,
        request_id: str,
        error_code: str,
    ) -> None:
        self._audit.append(
            action=AuditAction.WORK_ORDER_AI_ANALYSIS_FAILED,
            resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
            actor_user_id=None,
            resource_id=analysis.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
            details={
                "work_order_id": record.id,
                "analysis_id": analysis.id,
                "status": analysis.status,
                "error_code": error_code,
            },
        )


