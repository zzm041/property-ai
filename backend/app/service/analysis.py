"""Async AI analysis: snapshot, provider calls outside locks, then apply."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import transaction_context
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
    DEFAULT_CONFIDENCE_THRESHOLD,
    WORK_ORDER_ANALYSIS_PROMPT_VERSION,
    AiCallStatus,
    AnalysisStatus,
    DispatchCandidate,
    FilterReason,
    MappedAnalysis,
    ProviderType,
    WorkOrderAnalysisOutput,
    hard_filter_candidate,
    map_analysis_output,
    rank_candidates,
)
from app.domain.audit import AuditAction, AuditResourceType
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
from app.repository.ai import AiRepository
from app.repository.identity import IdentityRepository
from app.repository.work_order import WorkOrderRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

logger = get_logger(__name__)

_STAFF_ROLES = frozenset({"STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"})


@dataclass(frozen=True, slots=True)
class MediaRef:
    storage_key: str
    mime_type: str
    kind: str


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
    skipped: bool = False
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    asr_text: str | None
    vision_text: str | None
    llm_payload: Mapping[str, object] | None
    error_code: str | None
    call_status: str


class AnalysisService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        vlm: VLMProvider | None = None,
        asr: ASRProvider | None = None,
        clock: Callable[[], datetime] = utc_now,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._llm = llm or build_llm_provider(self._settings)
        self._vlm = vlm or build_vlm_provider(self._settings)
        self._asr = asr or build_asr_provider(self._settings)
        self._clock = clock
        self._work_orders = WorkOrderRepository(session)
        self._ai = AiRepository(session)
        self._audit = AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal

    def analyze_work_order(
        self,
        *,
        work_order_id: int,
        request_id: str,
        attempt: int = 1,
        actor: Principal | None = None,
    ) -> WorkOrderAnalysis | None:
        if actor is not None:
            self._authorize_review(actor, work_order_id)
        snapshot = self._prepare(work_order_id=work_order_id, request_id=request_id)
        if snapshot is None or snapshot.skipped:
            return self._ai.get_current_analysis(work_order_id)
        bundle = self._call_providers(snapshot=snapshot, request_id=request_id, attempt=attempt)
        return self._apply(
            snapshot=snapshot,
            bundle=bundle,
            request_id=request_id,
            attempt=attempt,
        )

    def get_analysis(
        self,
        *,
        actor: Principal,
        work_order_id: int,
    ) -> WorkOrderAnalysis | None:
        record = self._work_orders.get_work_order(work_order_id)
        if record is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_FOUND,
                message="Work order was not found",
                status_code=404,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        RbacService.require_permission(actor, AI_ANALYSIS_READ, scope)
        return self._ai.get_current_analysis(work_order_id)

    def _authorize_review(self, actor: Principal, work_order_id: int) -> None:
        record = self._work_orders.get_work_order(work_order_id)
        if record is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_FOUND,
                message="Work order was not found",
                status_code=404,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        RbacService.require_permission(actor, AI_ANALYSIS_REVIEW, scope)

    def _prepare(self, *, work_order_id: int, request_id: str) -> AnalysisSnapshot | None:
        del request_id
        try:
            with transaction_context(self._session):
                record = self._work_orders.get_work_order_for_update(work_order_id)
                if record is None:
                    return None
                content = self._work_orders.get_content(record.id)
                media_files = self._ai.list_media_with_files(record.id)
                images = tuple(
                    MediaRef(file_object.storage_key, file_object.mime_type, media.kind)
                    for media, file_object in media_files
                    if media.kind == MediaKind.IMAGE.value
                    and media.taken_phase == "REPORT"
                )
                audio = tuple(
                    MediaRef(file_object.storage_key, file_object.mime_type, media.kind)
                    for media, file_object in media_files
                    if media.kind == MediaKind.AUDIO.value
                    and media.taken_phase == "REPORT"
                )
                text = (None if content is None else content.text) or ""
                existing_asr = None if content is None else content.asr_text
                input_hash = sha256(
                    f"{record.id}|{text}|{existing_asr or ''}|{len(images)}|{len(audio)}".encode()
                ).hexdigest()
                current = self._ai.get_current_analysis_for_update(record.id)
                if current is not None and current.status in {
                    AnalysisStatus.COMPLETED.value,
                    AnalysisStatus.LOW_CONFIDENCE.value,
                }:
                    return AnalysisSnapshot(
                        work_order_id=record.id,
                        tenant_id=record.tenant_id,
                        property_id=record.property_id,
                        house_id=record.house_id,
                        building_id=self._ai.get_building_id(record.house_id),
                        analysis_id=current.id,
                        analysis_version=current.analysis_version,
                        work_order_version=record.version,
                        input_hash=input_hash,
                        text=text,
                        existing_asr_text=existing_asr,
                        images=images,
                        audio=audio,
                        skipped=True,
                        skip_reason="already_final",
                    )
                if current is not None and current.status == AnalysisStatus.PROCESSING.value:
                    current.input_hash = input_hash
                    analysis = current
                elif current is not None and current.status == AnalysisStatus.FAILED.value:
                    current.status = AnalysisStatus.PROCESSING.value
                    current.error_code = None
                    current.input_hash = input_hash
                    analysis = current
                elif current is not None and current.status == AnalysisStatus.STALE.value:
                    analysis = self._ai.add_analysis(
                        WorkOrderAnalysis(
                            tenant_id=record.tenant_id,
                            property_id=record.property_id,
                            work_order_id=record.id,
                            analysis_version=current.analysis_version + 1,
                            status=AnalysisStatus.PROCESSING.value,
                            input_hash=input_hash,
                            prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                        )
                    )
                else:
                    analysis = self._ai.add_analysis(
                        WorkOrderAnalysis(
                            tenant_id=record.tenant_id,
                            property_id=record.property_id,
                            work_order_id=record.id,
                            analysis_version=1,
                            status=AnalysisStatus.PROCESSING.value,
                            input_hash=input_hash,
                            prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                        )
                    )
                return AnalysisSnapshot(
                    work_order_id=record.id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                    house_id=record.house_id,
                    building_id=self._ai.get_building_id(record.house_id),
                    analysis_id=analysis.id,
                    analysis_version=analysis.analysis_version,
                    work_order_version=record.version,
                    input_hash=input_hash,
                    text=text,
                    existing_asr_text=existing_asr,
                    images=images,
                    audio=audio,
                )
        except IntegrityError:
            existing = self._ai.get_current_analysis(work_order_id)
            if existing is None:
                return None
            return AnalysisSnapshot(
                work_order_id=work_order_id,
                tenant_id=existing.tenant_id,
                property_id=existing.property_id,
                house_id=0,
                building_id=None,
                analysis_id=existing.id,
                analysis_version=existing.analysis_version,
                work_order_version=0,
                input_hash=existing.input_hash,
                text="",
                existing_asr_text=None,
                images=(),
                audio=(),
                skipped=True,
                skip_reason="duplicate",
            )

    def _call_providers(
        self,
        *,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> ProviderBundle:
        asr_text = snapshot.existing_asr_text
        vision_text = None
        error_code = None
        if snapshot.audio and asr_text is None:
            asr_text, asr_error = self._run_asr(snapshot, request_id, attempt)
            error_code = asr_error or error_code
        if snapshot.images:
            vision_text, vlm_error = self._run_vlm(snapshot, request_id, attempt)
            error_code = vlm_error or error_code
        payload, llm_error, call_status = self._run_llm(
            snapshot,
            asr_text=asr_text,
            vision_text=vision_text,
            request_id=request_id,
            attempt=attempt,
        )
        if llm_error is not None:
            error_code = llm_error
        return ProviderBundle(
            asr_text=asr_text,
            vision_text=vision_text,
            llm_payload=payload,
            error_code=error_code,
            call_status=call_status,
        )

    def _run_asr(
        self,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        media = snapshot.audio[0]
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
            )
            return None, exc.error_code

    def _run_vlm(
        self,
        snapshot: AnalysisSnapshot,
        request_id: str,
        attempt: int,
    ) -> tuple[str | None, str | None]:
        media = snapshot.images[0]
        started = perf_counter()
        started_at = self._clock()
        try:
            insight = self._vlm.inspect_image(
                storage_key=media.storage_key,
                mime_type=media.mime_type,
            )
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
            )
            return None, exc.error_code

    def _run_llm(
        self,
        snapshot: AnalysisSnapshot,
        *,
        asr_text: str | None,
        vision_text: str | None,
        request_id: str,
        attempt: int,
    ) -> tuple[Mapping[str, object] | None, str | None, str]:
        prompt = (
            f"text={snapshot.text};asr={asr_text or ''};vision={vision_text or ''}"
        )
        started = perf_counter()
        started_at = self._clock()
        try:
            payload = self._llm.structured_completion(
                prompt=prompt,
                prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION,
            )
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.LLM,
                operation="structured_completion",
                status=AiCallStatus.SUCCESS,
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=getattr(self._llm, "model_version", "unknown"),
            )
            return payload, None, AiCallStatus.SUCCESS.value
        except ProviderError as exc:
            status = _status_for_error(exc)
            self._write_call_log(
                snapshot,
                provider_type=ProviderType.LLM,
                operation="structured_completion",
                status=status,
                started_at=started_at,
                latency_ms=int((perf_counter() - started) * 1000),
                request_id=request_id,
                attempt=attempt,
                model_version=getattr(self._llm, "model_version", "unknown"),
                error_code=exc.error_code,
            )
            return None, exc.error_code, status.value

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
    ) -> None:
        finished_at = started_at + timedelta(milliseconds=max(latency_ms, 0))
        with transaction_context(self._session):
            self._ai.add_call_log(
                AiCallLog(
                    tenant_id=snapshot.tenant_id,
                    property_id=snapshot.property_id,
                    work_order_id=snapshot.work_order_id,
                    request_id=request_id,
                    provider_type=provider_type.value,
                    operation=operation,
                    biz_type="WORK_ORDER_ANALYSIS",
                    prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                    model_version=model_version,
                    status=status.value,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    attempt=attempt,
                    error_code=error_code,
                    token_usage=None,
                    input_hash=snapshot.input_hash,
                )
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
            with transaction_context(self._session):
                record = self._work_orders.get_work_order_for_update(snapshot.work_order_id)
                analysis = self._ai.get_current_analysis_for_update(snapshot.work_order_id)
                if record is None or analysis is None:
                    return None
                if analysis.id != snapshot.analysis_id:
                    return analysis
                if analysis.status != AnalysisStatus.PROCESSING.value:
                    return analysis
                content = self._work_orders.get_content(record.id)
                if content is not None and bundle.asr_text:
                    content.asr_text = bundle.asr_text
                stale = record.status != WorkOrderStatus.PENDING_ANALYSIS.value
                mapped, mapped_error = self._validate_payload(bundle.llm_payload)
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
                if mapped is None:
                    analysis.status = AnalysisStatus.FAILED.value
                    analysis.error_code = mapped_error or bundle.error_code or AiErrorCode.PROVIDER_FAILED
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
                    analysis.error_code = AiErrorCode.LOW_CONFIDENCE
                else:
                    analysis.status = AnalysisStatus.COMPLETED.value
                    analysis.error_code = None
                    record.category = mapped.category.value
                    record.severity = mapped.severity.value
                    record.priority = mapped.priority.value
                    self._transition_analyzed(record, request_id)
                self._write_recommendations(record, analysis, mapped, snapshot.building_id)
                self._append_success_audit(record, analysis, request_id, mapped)
                if analysis.status == AnalysisStatus.COMPLETED.value:
                    from app.service.incident import IncidentService

                    IncidentService(self._session, settings=self._settings).aggregate_locked(
                        record,
                        request_id=request_id,
                    )
                return analysis
        except AppError:
            raise
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="AI analysis could not be stored",
                status_code=500,
            ) from exc

    def _validate_payload(
        self,
        payload: Mapping[str, object] | None,
    ) -> tuple[MappedAnalysis | None, str | None]:
        if payload is None:
            return None, None
        try:
            output = WorkOrderAnalysisOutput.model_validate(payload)
            mapped = map_analysis_output(
                output,
                threshold=self._settings.ai_confidence_threshold
                or DEFAULT_CONFIDENCE_THRESHOLD,
            )
        except (ValidationError, ValueError):
            return None, AiErrorCode.INVALID_OUTPUT
        return mapped, None

    @staticmethod
    def _fill_analysis(analysis: WorkOrderAnalysis, mapped: MappedAnalysis) -> None:
        analysis.category = mapped.category.value
        analysis.subcategory = mapped.subcategory
        analysis.severity = mapped.severity.value
        analysis.priority = mapped.priority.value
        analysis.summary = mapped.summary
        analysis.risk = mapped.risk.value
        analysis.required_role = mapped.required_role
        analysis.required_skill = mapped.required_skill
        analysis.confidence = Decimal(str(mapped.confidence))

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
            roles = self._ai.list_employee_role_codes(employee.user_id)
            candidate = DispatchCandidate(
                employee_id=employee.id,
                duty_codes=self._ai.list_employee_duties(employee.id),
                skill_codes=self._ai.list_employee_skills(employee.id),
                current_load=0 if daily is None else daily.current_load,
                max_capacity=employee.max_capacity,
                on_duty=daily is not None
                and daily.on_duty == AttendanceOnDuty.ON_DUTY.value,
                employee_active=employee.status == EmployeeStatus.ACTIVE.value,
                property_effective=assignment is not None,
                has_staff_role=bool(roles & _STAFF_ROLES),
                same_building_history=self._ai.count_same_building_history(
                    employee_id=employee.id,
                    building_id=building_id,
                    tenant_id=record.tenant_id,
                    property_id=record.property_id,
                ),
                primary_incident_handler=False,
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
        ranked = rank_candidates(
            tuple(candidates),
            required_skill=mapped.required_skill,
            required_duty=mapped.required_duty,
        )
        rank_no = 1
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
                    reason_codes=list(reasons),
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
                    reason_codes=[reason.value],
                    filter_reason=reason.value,
                )
            )

    def list_recommendations(
        self,
        *,
        actor: Principal,
        work_order_id: int,
    ) -> tuple[DispatchRecommendation, ...]:
        record = self._work_orders.get_work_order(work_order_id)
        if record is None:
            raise AppError(
                code=WorkOrderErrorCode.NOT_FOUND,
                message="Work order was not found",
                status_code=404,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=record.tenant_id,
            property_id=record.property_id,
        )
        RbacService.require_permission(actor, DISPATCH_RECOMMENDATION_READ, scope)
        return tuple(
            item
            for item in self._ai.list_recommendations(work_order_id)
            if item.eligible == 1
        )

    def mark_assignment_choice(
        self,
        *,
        work_order: WorkOrder,
        employee_id: int,
        actor_user_id: int,
        request_id: str,
    ) -> None:
        recommendations = self._ai.list_recommendations(work_order.id)
        if not recommendations:
            return
        top_id = None
        for item in recommendations:
            if item.eligible == 1 and item.rank_no == 1:
                top_id = item.employee_id
            if item.employee_id == employee_id:
                item.selected = 1
        overridden = top_id is not None and top_id != employee_id
        self._audit.append(
            action=AuditAction.AI_RECOMMENDATION_REVIEWED,
            resource_type=AuditResourceType.DISPATCH_RECOMMENDATION,
            actor_user_id=actor_user_id,
            resource_id=work_order.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=work_order.tenant_id,
            property_id=work_order.property_id,
            details={
                "work_order_id": work_order.id,
                "employee_id": employee_id,
                "recommended_employee_id": top_id,
                "overridden": overridden,
            },
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


def _status_for_error(error: ProviderError) -> AiCallStatus:
    if isinstance(error, ProviderTimeoutError):
        return AiCallStatus.TIMEOUT
    if isinstance(error, ProviderInvalidOutputError):
        return AiCallStatus.INVALID
    if isinstance(error, (ProviderUnavailableError, ProviderRateLimitError)):
        return AiCallStatus.FAILED
    return AiCallStatus.FAILED
