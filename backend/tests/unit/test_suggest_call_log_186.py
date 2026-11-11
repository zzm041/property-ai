"""t186: `/work-orders/suggest` must leave an ai_call_logs row per request.

The pre-fill runs the same VLM/ASR contract the post-submit analysis uses, but
until t186 it only wrote a structured application log, so "which model call
produced this text" could not be reconciled per request. These tests pin the
row's shape (provider type, operation, model, latency, request id) and, just as
importantly, that an audit failure never turns a usable pre-fill into an error.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.core.rate_limit import MemoryRateLimiter
from app.domain.ai import AiCallStatus, ProviderType
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.ai import AiCallLog
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError
from app.provider.vision import VisionInsight
from app.service.work_order import description_suggestion as module
from app.service.work_order.description_suggestion import (
    SUGGEST_CALL_LOG_BIZ_TYPE,
    SUGGEST_CALL_LOG_OPERATION,
    DescriptionSuggestionService,
)


def resident(*, user_id: int = 6, tenant: int = 1) -> Principal:
    scope = OrganizationScope(ScopeType.TENANT, tenant_id=tenant)
    return Principal(
        user_id=user_id,
        status="ACTIVE",
        display_name="186 synthetic resident",
        grants=(RoleGrant("RESIDENT", scope, frozenset({"work_order:create"})),),
    )


def media(
    *,
    file_id: int = 11,
    mime_type: str = "image/jpeg",
    tenant_id: int = 1,
    scanner_status: str = "skipped_dev",
):
    return SimpleNamespace(
        id=file_id,
        mime_type=mime_type,
        uploader_user_id=6,
        tenant_id=tenant_id,
        property_id=7,
        storage_key=f"tenant/{tenant_id}/file/{file_id}",
        scanner_status=scanner_status,
        content_hash="a" * 64,
        size_bytes=1024,
    )


class StubVLM:
    model_version = "stub-vlm"

    def __init__(self, *, description: str = "白色PVC排水管底部弯管处有一滴水", error=None):
        self.description = description
        self.error = error

    def inspect_image(self, *, storage_key: str, mime_type: str):
        if self.error is not None:
            raise self.error
        return VisionInsight(
            description=self.description,
            labels=("pipe",),
            confidence=0.8,
            model_version=self.model_version,
        )


class StubASR:
    model_version = "stub-asr"

    def __init__(self, *, text: str = "厨房水龙头一直滴水", error=None):
        self.text = text
        self.error = error

    def transcribe(self, *, storage_key: str, mime_type: str):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, confidence=0.9, model_version=self.model_version)


class RecordingAiRepository:
    def __init__(self) -> None:
        self.records: list[AiCallLog] = []

    def add_call_log(self, record: AiCallLog) -> AiCallLog:
        record.id = len(self.records) + 1
        self.records.append(record)
        return record


def build_service(*, files=(), vlm=None, asr=None, ai=None, settings=None):
    settings = settings or Settings(_env_file=None, file_scanner_provider="development")
    service = DescriptionSuggestionService(
        MagicMock(),
        settings=settings,
        vlm=vlm or StubVLM(),
        asr=asr or StubASR(),
        limiter=MemoryRateLimiter(),
    )
    service._repository = MagicMock()
    lookup = {item.id: item for item in files}
    service._repository.get_file.side_effect = lambda file_id: lookup.get(file_id)
    service._ai = ai if ai is not None else RecordingAiRepository()
    return service


@pytest.fixture(autouse=True)
def _no_real_transaction(monkeypatch):
    # The service writes the audit row inside a session that owns its own
    # transaction; here that session is the request's double.
    monkeypatch.setattr(module, "begin_owned_transaction", lambda session: nullcontext())
    monkeypatch.setattr(
        DescriptionSuggestionService,
        "_audit_transaction",
        lambda self: nullcontext(self._session),
    )
    monkeypatch.setattr(
        DescriptionSuggestionService,
        "_write_provider_mode",
        lambda self, call_log_id, kind, *, session=None: None,
    )


def run(service, *, file_ids=(11,), request_id="186-request"):
    return service.suggest(actor=resident(), file_ids=file_ids, request_id=request_id)


# ------------------------------------------------------------------ happy path
def test_photo_suggestion_writes_one_audit_row():
    ai = RecordingAiRepository()
    service = build_service(files=(media(),), ai=ai)

    result = run(service, request_id="186-vlm-ok")

    assert result.text
    assert len(ai.records) == 1
    row = ai.records[0]
    assert row.provider_type == ProviderType.VLM.value
    assert row.operation == SUGGEST_CALL_LOG_OPERATION
    assert row.biz_type == SUGGEST_CALL_LOG_BIZ_TYPE
    assert row.status == AiCallStatus.SUCCESS.value
    assert row.model_version == "stub-vlm"
    assert row.request_id == "186-vlm-ok"
    assert row.tenant_id == 1
    assert row.property_id == 7
    assert row.work_order_id is None
    assert row.error_code is None
    assert row.attempt == 1
    assert row.latency_ms >= 0
    assert len(row.input_hash) == 64
    # A media-only pre-fill must not seed the PA-169 text quarantine probe.
    assert row.content_fingerprint is None
    # The suggested text itself never leaves the media boundary.
    assert "白色PVC" not in str(row.input_hash)


def test_voice_suggestion_records_the_asr_provider():
    ai = RecordingAiRepository()
    service = build_service(files=(media(file_id=21, mime_type="audio/mpeg"),), ai=ai)

    run(service, file_ids=(21,), request_id="186-asr-ok")

    assert [row.provider_type for row in ai.records] == [ProviderType.ASR.value]
    assert ai.records[0].model_version == "stub-asr"
    assert ai.records[0].prompt_version is None


def test_photo_row_carries_the_vision_prompt_version():
    from app.domain.work_order import VISION_ANALYSIS_PROMPT_VERSION

    ai = RecordingAiRepository()
    service = build_service(files=(media(),), ai=ai)

    run(service)

    assert ai.records[0].prompt_version == VISION_ANALYSIS_PROMPT_VERSION


# --------------------------------------------------------------------- failures
def test_provider_timeout_is_recorded_as_timeout():
    ai = RecordingAiRepository()
    service = build_service(files=(media(),), vlm=StubVLM(error=ProviderTimeoutError("slow")), ai=ai)

    result = run(service)

    assert result.text == ""
    assert len(ai.records) == 1
    row = ai.records[0]
    assert row.status == AiCallStatus.TIMEOUT.value
    assert row.error_code == "AI.TIMEOUT"


def test_provider_unavailable_is_recorded_as_failed():
    ai = RecordingAiRepository()
    service = build_service(
        files=(media(file_id=21, mime_type="audio/mpeg"),),
        asr=StubASR(error=ProviderUnavailableError("down")),
        ai=ai,
    )

    result = run(service, file_ids=(21,))

    assert result.reason_code == "AI.UNAVAILABLE"
    assert [row.status for row in ai.records] == [AiCallStatus.FAILED.value]
    assert ai.records[0].error_code == "AI.UNAVAILABLE"


def test_blank_description_is_recorded_as_invalid():
    ai = RecordingAiRepository()
    service = build_service(files=(media(),), vlm=StubVLM(description="   "), ai=ai)

    result = run(service)

    assert result.reason_code == "AI.INVALID_OUTPUT"
    assert [row.status for row in ai.records] == [AiCallStatus.INVALID.value]
    assert ai.records[0].error_code == "AI.INVALID_OUTPUT"


# ------------------------------------------------------- audit must never block
def test_audit_failure_still_returns_the_suggestion():
    class ExplodingRepo:
        def add_call_log(self, record):  # noqa: D401 - test double
            raise RuntimeError("audit table is unavailable")

    service = build_service(files=(media(),), ai=ExplodingRepo())

    result = run(service)

    assert result.text
    assert result.reason_code is None


def test_unscanned_media_never_reaches_the_provider_or_the_audit():
    ai = RecordingAiRepository()
    service = build_service(
        files=(media(scanner_status="pending"),),
        vlm=StubVLM(),
        ai=ai,
    )

    result = run(service)

    assert result.text == ""
    assert ai.records == []


# ------------------------------------------------- T192-2: the open-transaction branch
# The fixture above replaces `_audit_transaction` with a nullcontext, which is
# fine for the shape tests but leaves the branch that t186 actually broke
# uncovered: a request session that already holds a transaction. The row has to
# be committed on a session of its own, or a read-only request swallows it.
REAL_AUDIT_TRANSACTION = DescriptionSuggestionService._audit_transaction
REAL_BEGIN_OWNED_TRANSACTION = module.begin_owned_transaction


def audit_engine(path):
    """sqlite table shaped from the model, minus the MySQL-only constraints."""

    engine = create_engine(f"sqlite:///{path}")
    columns = [
        f'"{column.name}" {column.type.compile(engine.dialect)}'
        for column in AiCallLog.__table__.columns
        if column.name != "id"
    ]
    statement = (
        'CREATE TABLE "ai_call_logs" '
        f'(id INTEGER PRIMARY KEY AUTOINCREMENT, {", ".join(columns)})'
    )
    with engine.begin() as connection:
        connection.execute(text(statement))
    return engine


def test_audit_row_lands_in_its_own_session_when_the_request_transaction_is_open(
    tmp_path, monkeypatch
):
    engine = audit_engine(tmp_path / "audit.db")
    request_session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    request_session.execute(text("select 1"))
    assert request_session.in_transaction() is True

    # Undo the module-wide doubles: this test needs the real branch.
    monkeypatch.setattr(module, "begin_owned_transaction", REAL_BEGIN_OWNED_TRANSACTION)
    monkeypatch.setattr(
        DescriptionSuggestionService, "_audit_transaction", REAL_AUDIT_TRANSACTION
    )

    service = DescriptionSuggestionService(
        request_session,
        settings=Settings(_env_file=None, file_scanner_provider="development"),
        vlm=StubVLM(),
        asr=StubASR(),
        limiter=MemoryRateLimiter(),
    )
    service._repository = MagicMock()
    service._repository.get_file.side_effect = lambda file_id: media(file_id=file_id)
    recording = RecordingAiRepository()
    service._ai = recording

    result = service.suggest(
        actor=resident(), file_ids=(11,), request_id="t192-2-open-transaction"
    )

    assert result.text
    # Nothing went through the request session, and it is still open.
    assert recording.records == []
    assert request_session.in_transaction() is True

    with Session(bind=engine) as fresh:
        rows = fresh.execute(
            text(
                'select request_id, provider_type, operation, status, biz_type '
                'from "ai_call_logs"'
            )
        ).all()
    assert rows == [
        (
            "t192-2-open-transaction",
            "VLM",
            SUGGEST_CALL_LOG_OPERATION,
            "SUCCESS",
            SUGGEST_CALL_LOG_BIZ_TYPE,
        )
    ]

    # Rolling the request back must not take the audit row with it.
    request_session.rollback()
    with Session(bind=engine) as fresh:
        assert fresh.scalar(text('select count(*) from "ai_call_logs"')) == 1
