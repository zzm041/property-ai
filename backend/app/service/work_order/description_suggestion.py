"""Resident-facing description suggestion from one uploaded photo or voice note.

The suggestion only pre-fills the report form. It never decides category,
severity, dispatch, or whether a WorkOrder may be created, and every AI-side
failure degrades to an empty text plus a reason code instead of an error, so a
resident can always keep typing by hand.

Model access goes through the existing VLM/ASR providers (same contracts the
post-submit analysis uses); this module adds no new model call path.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Sequence

from sqlalchemy.orm import Session

from app.core.config import Settings
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
from app.domain.work_order import MediaKind
from app.model.work_order import FileObject
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
        return self._explained(self._suggest_from(record, source))

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
    def _suggest_from(self, record: FileObject, source: str) -> DescriptionSuggestion:
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
        try:
            text = self._read_text(record, source)
        except ProviderError as exc:
            self._log_call(record, source, exc.error_code, started, 0)
            return DescriptionSuggestion(text="", source="none", reason_code=exc.error_code)
        cleaned = self._clean(text)
        if not cleaned:
            self._log_call(record, source, "AI.INVALID_OUTPUT", started, 0)
            return DescriptionSuggestion(
                text="", source="none", reason_code="AI.INVALID_OUTPUT"
            )
        self._log_call(record, source, None, started, len(cleaned))
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
