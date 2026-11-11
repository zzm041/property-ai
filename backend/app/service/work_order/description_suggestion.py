"""Resident-facing description suggestion from one uploaded photo or voice note.

The suggestion only pre-fills the report form. It never decides category,
severity, dispatch, or whether a WorkOrder may be created, and every AI-side
failure degrades to an empty text plus a reason code instead of an error, so a
resident can always keep typing by hand.

Model access goes through the existing VLM/ASR providers (same contracts the
post-submit analysis uses); this module adds no new model call path.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Sequence

from sqlalchemy.orm import Session

from sqlalchemy import MetaData, Table, inspect as sa_inspect, update

from app.core.config import Settings
from app.core.database import begin_owned_transaction
from app.core.errors import (
    AppError,
    AuthErrorCode,
    FileErrorCode,
    IamErrorCode,
    WorkOrderErrorCode,
)
from app.core.logging import get_logger
from app.core.rate_limit import MemoryRateLimiter, RateLimiter, RedisRateLimiter
from app.domain.identity import Principal
from app.domain.ai import AiCallStatus, ProviderType
from app.domain.work_order import VISION_ANALYSIS_PROMPT_VERSION, MediaKind
from app.model.ai import AiCallLog
from app.model.work_order import FileObject
from app.core.time import utc_now
from app.repository.ai import AiRepository
from app.provider.asr import ASRProvider
from app.provider.errors import ProviderError
from app.provider.factory import build_asr_provider, build_vlm_provider
from app.service.work_order.media_scan import admit_scanned_media, log_media_scan_skipped
from app.provider.vision import VLMProvider
from app.repository.work_order import WorkOrderRepository

logger = get_logger(__name__)

#: Hard ceiling for one suggestion, independent of the longer analysis timeouts.
SUGGEST_TIMEOUT_SECONDS = 15.0
#: Same allow-list the WorkOrder media pipeline enforces (see work_order.py).
SUGGESTIBLE_MIME = {
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "audio/mpeg": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO,
    "audio/wav": MediaKind.AUDIO,
    "audio/webm": MediaKind.AUDIO,
}
#: D6: `skipped_dev` is *not* ready by itself - the admission rule decides.
MAX_SUGGESTED_TEXT = 200
RESIDENT_ROLE = "RESIDENT"
SUGGEST_TEXT_KIND = {"vlm": "image", "asr": "audio"}
#: PA-178 (t186): the suggest call is auditable per request, exactly like the
#: post-submit analysis, so "which model call produced this pre-fill" is a
#: query on ai_call_logs instead of a guess from app logs.
SUGGEST_CALL_LOG_BIZ_TYPE = "WORK_ORDER_SUGGEST"
SUGGEST_CALL_LOG_OPERATION = "suggest_description"
#: PA-163: a machine code alone leaves the reporter staring at an empty box, so
#: every returned ``reason_code`` carries an operator/reporter readable sentence.
#: The wording never claims a scan happened; it only states what to do next.
SUGGESTION_REASONS = {
    "MEDIA_NOT_READY": (
        "媒体文件还没有通过安全扫描（scanner_status=pending），暂时无法识别，请手动填写。"
    ),
    "MEDIA_SCAN_UNAVAILABLE": (
        "本部署没有配置文件安全扫描器，上传的媒体无法使用，请手动填写。"
    ),
    "MEDIA_SCAN_STUB_UNACKNOWLEDGED": (
        "本部署用的是开发用扫描桩、没有真正扫描，且未声明演示模式，"
        "上传的媒体不能用于识别，请手动填写。"
    ),
    "MEDIA_SCAN_BLOCKED": "上传的媒体没有通过安全扫描，不能用于识别，请手动填写。",
    "MEDIA_SCAN_UNKNOWN": "无法确认上传媒体的扫描状态，请手动填写。",
    "AI.UNAVAILABLE": "描述识别服务当前不可用，请手动填写。",
    "AI.TIMEOUT": "描述识别超时，请手动填写。",
    "AI.INVALID_OUTPUT": "模型没有给出可用的描述，请手动填写。",
    "AI.RATE_LIMITED": "描述识别请求过于频繁，请稍后重试或手动填写。",
}
SUGGESTION_REASON_FALLBACK = "未能生成描述，请手动填写。"


def suggestion_reason(reason_code: str | None) -> str | None:
    """Turn a machine reason code into one sentence a reporter can act on."""

    if reason_code is None:
        return None
    return SUGGESTION_REASONS.get(reason_code, SUGGESTION_REASON_FALLBACK)


@dataclass(frozen=True, slots=True)
class DescriptionSuggestion:
    """What the form may pre-fill. ``source`` is honest about who produced it."""

    text: str
    source: str
    reason_code: str | None = None
    reason: str | None = None


class DescriptionSuggestionService:
    """Suggest text for the report form; never authorizes or creates anything."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        vlm: VLMProvider | None = None,
        asr: ASRProvider | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repository = WorkOrderRepository(session)
        self._ai = AiRepository(session)
        # The analysis path keeps its own (longer) deadlines; only this call is
        # bounded by SUGGEST_TIMEOUT_SECONDS.
        bounded = settings.model_copy(
            update={
                "vlm_timeout_seconds": min(
                    settings.vlm_timeout_seconds, SUGGEST_TIMEOUT_SECONDS
                ),
                "asr_timeout_seconds": min(
                    settings.asr_timeout_seconds, SUGGEST_TIMEOUT_SECONDS
                ),
            }
        )
        self._vlm = vlm if vlm is not None else build_vlm_provider(bounded)
        self._asr = asr if asr is not None else build_asr_provider(bounded)
        if limiter is not None:
            self._limiter = limiter
        elif settings.app_env == "test":
            self._limiter = MemoryRateLimiter()
        else:
            # Same Redis limiter used by the credential flows; it fails closed.
            self._limiter = RedisRateLimiter(settings.redis_url)

    def suggest(
        self,
        *,
        actor: Principal,
        file_ids: Sequence[int],
        request_id: str,
    ) -> DescriptionSuggestion:
        self._require_resident(actor)
        self._require_request_id(request_id)
        normalized = self._normalize_file_ids(file_ids)
        self._require_rate_limit(actor)
        target = self._select_target(actor, normalized)
        if target is None:
            # PA-163: only a media file that is not usable yet gets here; say so in
            # words instead of handing the form an unexplained empty string.
            return self._explained(
                DescriptionSuggestion(text="", source="none", reason_code="MEDIA_NOT_READY")
            )
        record, source = target
        return self._explained(
            self._suggest_from(record, source, request_id=request_id)
        )

    @staticmethod
    def _explained(result: DescriptionSuggestion) -> DescriptionSuggestion:
        """Attach the human sentence matching the machine reason code."""

        return replace(result, reason=suggestion_reason(result.reason_code))

    # ------------------------------------------------------------------ guards
    @staticmethod
    def _require_resident(actor: Principal) -> None:
        if not isinstance(actor, Principal) or actor.user_id <= 0:
            raise AppError(
                code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403
            )
        if actor.status != "ACTIVE":
            raise AppError(
                code=AuthErrorCode.USER_DISABLED,
                message="Permission denied",
                status_code=401,
            )
        if RESIDENT_ROLE not in actor.roles:
            raise AppError(
                code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403
            )

    @staticmethod
    def _require_request_id(request_id: str) -> None:
        if not isinstance(request_id, str) or not request_id.strip():
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="request_id is required",
                status_code=422,
            )

    @staticmethod
    def _normalize_file_ids(file_ids: Sequence[int]) -> tuple[int, ...]:
        if not file_ids:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="file_ids must reference at least one uploaded file",
                status_code=422,
                details={"field": "file_ids"},
            )
        unique: list[int] = []
        for file_id in file_ids:
            if not isinstance(file_id, int) or isinstance(file_id, bool) or file_id <= 0:
                raise AppError(
                    code=WorkOrderErrorCode.INVALID_INPUT,
                    message="file_ids must be a positive integer",
                    status_code=422,
                    details={"field": "file_ids"},
                )
            if file_id not in unique:
                unique.append(file_id)
        if len(unique) > 4:
            raise AppError(
                code=WorkOrderErrorCode.INVALID_INPUT,
                message="At most 4 files can be suggested in one request",
                status_code=422,
                details={"field": "file_ids"},
            )
        return tuple(unique)

    def _require_rate_limit(self, actor: Principal) -> None:
        limit = self._settings.wo_suggest_limit_per_hour
        window = 3600
        if not self._limiter.hit(
            f"wo:suggest:{actor.user_id}", limit=limit, window_seconds=window
        ):
            raise AppError(
                code=WorkOrderErrorCode.SUGGEST_RATE_LIMITED,
                message="Description suggestions are rate limited",
                status_code=429,
                details={"limit": limit, "window_seconds": window},
            )

    # ----------------------------------------------------------------- loading
    def _select_target(
        self, actor: Principal, file_ids: tuple[int, ...]
    ) -> tuple[FileObject, str] | None:
        """Pick one media object: photos win over voice notes, order preserved."""

        seen: list[tuple[FileObject, str]] = []
        for file_id in file_ids:
            record = self._repository.get_file(file_id)
            if record is None or record.uploader_user_id != actor.user_id:
                raise AppError(
                    code=FileErrorCode.NOT_FOUND,
                    message="File was not found",
                    status_code=404,
                )
            if not self._actor_can_see_tenant(actor, record.tenant_id):
                raise AppError(
                    code=FileErrorCode.NOT_FOUND,
                    message="File was not found",
                    status_code=404,
                )
            kind = SUGGESTIBLE_MIME.get(record.mime_type)
            if kind is None:
                raise AppError(
                    code=FileErrorCode.UNSUPPORTED_TYPE,
                    message="File type is not allowed",
                    status_code=422,
                )
            seen.append((record, "vlm" if kind is MediaKind.IMAGE else "asr"))
        for record, source in seen:
            if source == "vlm":
                return record, source
        for record, source in seen:
            return record, source
        return None

    @staticmethod
    def _actor_can_see_tenant(actor: Principal, tenant_id: int) -> bool:
        return any(grant.scope.tenant_id == tenant_id for grant in actor.grants)

    # ----------------------------------------------------------------- calling
    def _suggest_from(
        self, record: FileObject, source: str, *, request_id: str | None = None
    ) -> DescriptionSuggestion:
        # D6: never treat an unscanned file as ready; the shared rule names both
        # the verdict and the reason a reporter should be told.
        admission = admit_scanned_media(record.scanner_status, self._settings)
        if not admission.usable:
            return DescriptionSuggestion(
                text="", source="none", reason_code=admission.reason_code
            )
        if admission.demo_stub_skip:
            log_media_scan_skipped(logger, file_id=record.id, admission=admission)
        if not record.storage_key:
            return DescriptionSuggestion(
                text="", source="none", reason_code="MEDIA_NOT_READY"
            )
        started = perf_counter()
        started_at = utc_now()
        try:
            text = self._read_text(record, source)
        except ProviderError as exc:
            self._log_call(record, source, exc.error_code, started, 0)
            self._record_call_log(
                record,
                source,
                request_id=request_id,
                started=started,
                started_at=started_at,
                error=exc,
                error_code=exc.error_code,
            )
            return DescriptionSuggestion(text="", source="none", reason_code=exc.error_code)
        cleaned = self._clean(text)
        if not cleaned:
            self._log_call(record, source, "AI.INVALID_OUTPUT", started, 0)
            self._record_call_log(
                record,
                source,
                request_id=request_id,
                started=started,
                started_at=started_at,
                error=None,
                error_code="AI.INVALID_OUTPUT",
                status=AiCallStatus.INVALID,
            )
            return DescriptionSuggestion(
                text="", source="none", reason_code="AI.INVALID_OUTPUT"
            )
        self._log_call(record, source, None, started, len(cleaned))
        self._record_call_log(
            record,
            source,
            request_id=request_id,
            started=started,
            started_at=started_at,
            error=None,
            error_code=None,
        )
        return DescriptionSuggestion(text=cleaned, source=source)

    def _read_text(self, record: FileObject, source: str) -> str:
        if source == "vlm":
            insight = self._vlm.inspect_image(
                storage_key=record.storage_key, mime_type=record.mime_type
            )
            return insight.description
        transcript = self._asr.transcribe(
            storage_key=record.storage_key, mime_type=record.mime_type
        )
        return transcript.text

    @staticmethod
    def _clean(text: object) -> str:
        if not isinstance(text, str):
            return ""
        collapsed = " ".join(text.split())
        return collapsed[:MAX_SUGGESTED_TEXT].strip()

    def _log_call(
        self,
        record: FileObject,
        source: str,
        reason_code: str | None,
        started: float,
        text_length: int,
    ) -> None:
        # Never log the suggested text itself: it is derived from private media.
        logger.info(
            "work_order_description_suggested",
            file_id=record.id,
            media_kind=SUGGEST_TEXT_KIND[source],
            provider="vlm" if source == "vlm" else "asr",
            status="success" if reason_code is None else "failed",
            reason_code=reason_code,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            text_length=text_length,
            media_digest=sha256(
                f"{record.id}|{record.mime_type}|{record.content_hash}".encode("utf-8")
            ).hexdigest()[:16],
        )

    @contextmanager
    def _audit_transaction(self):
        """Yield the session that owns the audit row's transaction.

        The request path already read through this session, so its implicit
        transaction is still open and a writer-owned boundary cannot start on
        it. In that case the row lives on a short, separate session: an audit
        row that only commits with somebody else's transaction would vanish on
        a read-only request, which is exactly what t186 measured.
        """

        session = self._session
        if not session.in_transaction():
            with begin_owned_transaction(session):
                yield session
            return
        bind = session.get_bind()
        owned = Session(bind=bind, autoflush=False, expire_on_commit=False)
        try:
            with begin_owned_transaction(owned):
                yield owned
        finally:
            owned.close()

    def _ai_for(self, session):
        """Reuse the injected repository when the audit session is the request's."""

        return self._ai if session is self._session else AiRepository(session)

    def _record_call_log(
        self,
        record: FileObject,
        source: str,
        *,
        request_id: str | None,
        started: float,
        started_at,
        error: ProviderError | None,
        error_code: str | None,
        status: AiCallStatus | None = None,
    ) -> None:
        """Write the one ai_call_logs row this suggestion owes.

        Same shape as the analysis writer: provider type, operation, model,
        latency and request id, so a suggestion can be reconciled per request.
        The suggestion must stay a pre-fill even when auditing fails, so a
        database problem is logged and swallowed instead of turning a usable
        text into an error.
        """

        # The audit row is keyed by request; without one there is nothing to
        # reconcile, and inventing an id would poison that key.
        if request_id is None:
            return

        provider = self._vlm if source == "vlm" else self._asr
        provider_type = ProviderType.VLM if source == "vlm" else ProviderType.ASR
        resolved_status = status or self._status_for(error)
        finished_at = utc_now()
        latency_ms = max(int(round((perf_counter() - started) * 1000)), 0)
        try:
            with self._audit_transaction() as audit_session:
                call_log = self._ai_for(audit_session).add_call_log(
                    AiCallLog(
                        tenant_id=record.tenant_id,
                        property_id=record.property_id,
                        work_order_id=None,
                        request_id=request_id,
                        provider_type=provider_type.value,
                        operation=SUGGEST_CALL_LOG_OPERATION,
                        biz_type=SUGGEST_CALL_LOG_BIZ_TYPE,
                        prompt_version=self._prompt_version(source),
                        model_version=str(
                            getattr(provider, "model_version", None) or "unknown"
                        )[:64],
                        status=resolved_status.value,
                        started_at=started_at,
                        finished_at=finished_at,
                        latency_ms=latency_ms,
                        attempt=1,
                        error_code=error_code,
                        token_usage=None,
                        input_hash=sha256(
                            f"{record.id}|{record.mime_type}|{record.content_hash}".encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                        # PA-169 keys quarantine on report text; a suggestion is
                        # media-only, so it must not seed that probe.
                        content_fingerprint=None,
                    )
                )
                self._write_provider_mode(
                    call_log.id, provider_type, session=audit_session
                )
        except Exception as exc:  # pragma: no cover - audit must never break the form
            logger.warning(
                "work_order_suggestion_call_log_failed",
                file_id=record.id,
                provider_type=provider_type.value,
                reason_code=error_code,
                # Without the driver's message this warning is useless when the
                # row fails on a constraint, which is exactly how t186 found it.
                error=f"{type(exc).__name__}: {exc}"[:512],
            )

    @staticmethod
    def _status_for(error: ProviderError | None) -> AiCallStatus:
        if error is None:
            return AiCallStatus.SUCCESS
        name = type(error).__name__
        if "Timeout" in name:
            return AiCallStatus.TIMEOUT
        if "InvalidOutput" in name:
            return AiCallStatus.INVALID
        return AiCallStatus.FAILED

    @staticmethod
    def _prompt_version(source: str) -> str | None:
        if source == "vlm":
            return VISION_ANALYSIS_PROMPT_VERSION
        return None

    def _write_provider_mode(
        self,
        call_log_id: int,
        provider_type: ProviderType,
        *,
        session: Session | None = None,
    ) -> None:
        """Fill the 0009 provenance column without mapping it on shared 0007."""

        target = session if session is not None else self._session
        bind = target.get_bind()
        if bind is None:
            return
        columns = {
            column["name"]
            for column in sa_inspect(bind).get_columns("ai_call_logs")
        }
        if "provider_mode" not in columns:
            return
        configured = {
            ProviderType.VLM: self._settings.vlm_provider,
            ProviderType.ASR: self._settings.asr_provider,
        }[provider_type]
        table = Table("ai_call_logs", MetaData(), autoload_with=bind)
        target.execute(
            update(table)
            .where(table.c.id == call_log_id)
            .values(provider_mode=configured.upper())
        )
