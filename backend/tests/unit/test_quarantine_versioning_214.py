"""t214 / F-7 + F-6: the quarantine probe is version-scoped, headers are not evidence.

F-7 reproduced in t213: report content refused once by a *fixed-away* build stayed
refused for 24 hours, with a 0 ms provider-skipped call and ``AI.INPUT_QUARANTINED``.
The product was already green; E2E stayed red. The probe now asks "did *this* build,
under *this* prompt contract, refuse this content" - and an operator can clear a stale
refusal without deleting the audit row.

F-6: ``orchestrator`` glues append notes on with ``"\\nResident updates:\\n"``; the
sentence splitter turned that label into ``report:2``, an evidence item the model is
allowed to cite and did cite.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import Session

from app.core import buildinfo
from app.core.errors import AiErrorCode
from app.domain.ai import content_fingerprint
from app.domain.ai import rules
from app.model.ai import AiCallLog
from app.repository.ai import AiRepository

ROOT = Path(__file__).resolve().parents[3]
REPORT = "厨房水槽堵了，水流不下去，需要用工具疏通一下。"
# The model columns are UTCDateTime: every bound value must be timezone-aware.
REFUSED_AT = datetime(2026, 11, 12, 4, 52, 42, tzinfo=UTC)
PROMPT_VERSION = "wo-analysis-v26-summary-fidelity"


# --------------------------------------------------------------------------- sqlite
def _engine(path: Path):
    """sqlite copy of ``ai_call_logs`` / ``audit_logs``, shaped from the models."""

    engine = create_engine(f"sqlite:///{path}")
    for model in (AiCallLog, __import__("app.model.identity", fromlist=["AuditLog"]).AuditLog):
        columns = [
            f'"{column.name}" {column.type.compile(engine.dialect)}'
            for column in model.__table__.columns
            if column.name != "id"
        ]
        statement = (
            f'CREATE TABLE "{model.__tablename__}" '
            f'(id INTEGER PRIMARY KEY AUTOINCREMENT, {", ".join(columns)})'
        )
        with engine.begin() as connection:
            connection.execute(text(statement))
    return engine


def _refusal(
    session: Session,
    *,
    code_version: str | None = "build-A",
    prompt_version: str = PROMPT_VERSION,
    fingerprint: str | None = None,
    cleared: datetime | None = None,
    refused_at: datetime | None = None,
    tenant_id: int = 5,
) -> int:
    session.execute(
        insert(AiCallLog).values(
            tenant_id=tenant_id,
            property_id=5,
            work_order_id=628,
            request_id="t214",
            provider_type="LLM",
            provider_mode="production",
            operation="payload_validation",
            biz_type="WORK_ORDER_ANALYSIS",
            prompt_version=prompt_version,
            model_version="qwen3.5:4b",
            code_version=code_version,
            status="INVALID",
            started_at=refused_at or REFUSED_AT,
            finished_at=refused_at or REFUSED_AT,
            latency_ms=0,
            attempt=1,
            error_code=AiErrorCode.INVALID_OUTPUT,
            input_hash="b" * 64,
            content_fingerprint=fingerprint or content_fingerprint(REPORT),
            quarantine_cleared_at=cleared,
            created_at=refused_at or REFUSED_AT,
        )
    )
    session.commit()
    return int(session.scalar(text("select max(id) from ai_call_logs")) or 0)


def _probe(session: Session, *, code_version: str | None = "build-A", prompt_version: str = PROMPT_VERSION):
    return AiRepository(session).quarantined_output_for_content(
        content_fingerprint=content_fingerprint(REPORT),
        tenant_id=5,
        property_id=5,
        error_codes=(AiErrorCode.INVALID_OUTPUT, AiErrorCode.OUTPUT_UNVERIFIABLE),
        since=datetime(2026, 11, 1, tzinfo=UTC),
        code_version=code_version,
        prompt_version=prompt_version,
    )


# ------------------------------------------------------- F-7: probe is versioned
def test_the_same_build_and_prompt_still_quarantines(tmp_path):
    engine = _engine(tmp_path / "same-build.db")
    with Session(engine, future=True) as session:
        _refusal(session)
        hit = _probe(session)
    assert hit is not None
    assert hit[1] == 628


def test_a_different_code_version_lets_the_text_through(tmp_path):
    engine = _engine(tmp_path / "new-build.db")
    with Session(engine, future=True) as session:
        _refusal(session, code_version="build-A")
        assert _probe(session, code_version="build-B") is None
        # ... while the build that actually refused it still sees the refusal.
        assert _probe(session, code_version="build-A") is not None


def test_a_different_prompt_version_lets_the_text_through(tmp_path):
    engine = _engine(tmp_path / "new-prompt.db")
    with Session(engine, future=True) as session:
        _refusal(session)
        assert _probe(session, prompt_version="wo-analysis-v27") is None


def test_an_unversioned_refusal_is_always_re_asked(tmp_path):
    # Rows written before 20261112_0021 carry NULL: the exact case that has to be re-asked.
    engine = _engine(tmp_path / "unversioned.db")
    with Session(engine, future=True) as session:
        _refusal(session, code_version=None)
        assert _probe(session, code_version="build-A") is None


def test_an_operator_cleared_refusal_is_ignored(tmp_path):
    engine = _engine(tmp_path / "cleared.db")
    with Session(engine, future=True) as session:
        _refusal(session, cleared=datetime(2026, 11, 12, 6, 0, tzinfo=UTC))
        assert _probe(session) is None


def test_the_compiled_probe_names_the_version_and_the_clear_stamp():
    statements: list[str] = []

    class _Result:
        def first(self):
            return None

    class _Session:
        def execute(self, statement):
            statements.append(str(statement.compile()))
            return _Result()

    AiRepository(_Session()).quarantined_output_for_content(
        content_fingerprint=content_fingerprint(REPORT),
        tenant_id=5,
        error_codes=(AiErrorCode.INVALID_OUTPUT,),
        since=datetime(2026, 11, 1, tzinfo=UTC),
        code_version="build-A",
        prompt_version=PROMPT_VERSION,
    )
    compiled = statements[0]
    assert "ai_call_logs.code_version" in compiled
    assert "ai_call_logs.prompt_version" in compiled
    assert "ai_call_logs.quarantine_cleared_at IS NULL" in compiled
    assert "LIMIT" in compiled.upper()


def test_the_service_probe_passes_the_running_version(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from app.service.analysis import AnalysisService
    from app.service.analysis import orchestrator as orch

    seen: dict[str, object] = {}
    service = object.__new__(AnalysisService)
    service._session = None
    service._clock = lambda: datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **kwargs: seen.update(kwargs) or None
    )
    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    try:
        service._quarantine_for(
            SimpleNamespace(
                work_order_id=628,
                input_hash="a" * 64,
                tenant_id=5,
                property_id=5,
                text=REPORT,
                images=(),
                audio=(),
            )
        )
    finally:
        orch.shared.begin_owned_transaction = original

    assert seen["code_version"] == buildinfo.code_version()
    assert seen["prompt_version"] == orch.shared.WORK_ORDER_ANALYSIS_PROMPT_VERSION


def test_code_version_prefers_the_baked_sha_and_falls_back_to_the_tree(monkeypatch):
    buildinfo.code_version.cache_clear()
    monkeypatch.setattr(
        buildinfo, "build_info", lambda: {"source_sha": "abc123", "files": {}}
    )
    assert buildinfo.code_version() == "abc123"

    buildinfo.code_version.cache_clear()
    monkeypatch.setattr(
        buildinfo,
        "build_info",
        lambda: {
            "source_sha": buildinfo.UNKNOWN,
            "files": {"domain/ai/rules.py": {"sha256": "1" * 64}},
        },
    )
    fallback = buildinfo.code_version()
    # The column is VARCHAR(64): "tree-" plus a truncated tree digest.
    assert fallback.startswith("tree-") and len(fallback) == 64
    # Cached: a second call must not rehash (build_info is read once per process).
    assert buildinfo.code_version() == fallback
    buildinfo.code_version.cache_clear()


# --------------------------------------------------- F-7: operator clear + audit
def _load_quarantine_tool():
    path = ROOT / "scripts" / "ops" / "ai_quarantine.py"
    spec = importlib.util.spec_from_file_location("ai_quarantine_214", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_duration_parsing_is_explicit():
    tool = _load_quarantine_tool()
    assert tool.parse_duration("90") == timedelta(seconds=90)
    assert tool.parse_duration("30m") == timedelta(minutes=30)
    assert tool.parse_duration("24h") == timedelta(hours=24)
    assert tool.parse_duration("7d") == timedelta(days=7)
    with pytest.raises(ValueError, match="not a duration"):
        tool.parse_duration("soon")


def test_dry_run_reports_without_writing(tmp_path):
    tool = _load_quarantine_tool()
    engine = _engine(tmp_path / "dry-run.db")
    with Session(engine, future=True) as session:
        _refusal(session, refused_at=REFUSED_AT)
        report = tool.QuarantineClearer(session, dry_run=True).clear(
            older_than=timedelta(hours=1),
            content_fingerprint=None,
            tenant_id=None,
            limit=10,
            actor_user_id=None,
            reason="dry run",
        )
        assert report.selected == 1 and report.cleared == 0
        rows = session.execute(
            select(AiCallLog.quarantine_cleared_at, AiCallLog.id)
        ).all()
        assert [row[0] for row in rows] == [None]
        assert session.scalar(text("select count(*) from audit_logs")) == 0


def test_clearing_stamps_the_row_and_writes_one_audit_row(tmp_path):
    tool = _load_quarantine_tool()
    engine = _engine(tmp_path / "clear.db")
    with Session(engine, future=True) as session:
        call_id = _refusal(session, refused_at=REFUSED_AT)
        report = tool.QuarantineClearer(session, dry_run=False).clear(
            older_than=timedelta(hours=1),
            content_fingerprint=None,
            tenant_id=None,
            limit=10,
            actor_user_id=22,
            reason="t214 regression: the build that refused this text is gone",
        )
        assert report.cleared == 1
        assert report.fingerprints == [content_fingerprint(REPORT)]

        stamped = session.scalar(
            select(AiCallLog.quarantine_cleared_at).where(AiCallLog.id == call_id)
        )
        assert stamped is not None

        audits = session.execute(
            select(
                __import__("app.model.identity", fromlist=["AuditLog"]).AuditLog.action,
                __import__("app.model.identity", fromlist=["AuditLog"]).AuditLog.resource_id,
                __import__("app.model.identity", fromlist=["AuditLog"]).AuditLog.details_json,
                __import__("app.model.identity", fromlist=["AuditLog"]).AuditLog.actor_user_id,
            )
        ).all()
        assert len(audits) == 1
        action, resource_id, details, actor = audits[0]
        assert action == tool.AUDIT_ACTION
        assert resource_id == call_id
        assert actor == 22
        assert details["content_fingerprint"] == content_fingerprint(REPORT)
        assert details["refusal_code_version"] == "build-A"
        assert details["prompt_version"] == PROMPT_VERSION
        assert "t214 regression" in details["reason"]

        # The row survives: clearing is a stamp, never a delete.
        assert session.scalar(text("select count(*) from ai_call_logs")) == 1
        # ... and the probe now lets the text through to the model.
        assert _probe(session) is None


def test_clearing_respects_the_fingerprint_and_tenant_filters(tmp_path):
    tool = _load_quarantine_tool()
    engine = _engine(tmp_path / "filters.db")
    with Session(engine, future=True) as session:
        _refusal(session, refused_at=REFUSED_AT)
        _refusal(
            session,
            fingerprint="0" * 64,
            tenant_id=9,
            refused_at=REFUSED_AT,
        )
        report = tool.QuarantineClearer(session, dry_run=False).clear(
            older_than=timedelta(hours=1),
            content_fingerprint=content_fingerprint(REPORT),
            tenant_id=5,
            limit=10,
            actor_user_id=None,
            reason="scoped",
        )
        assert report.cleared == 1
        remaining = session.execute(select(AiCallLog.tenant_id, AiCallLog.quarantine_cleared_at)).all()
        assert sorted((row[0], row[1] is None) for row in remaining) == [(5, False), (9, True)]


def test_recent_refusals_inside_the_cutoff_are_left_alone(tmp_path):
    tool = _load_quarantine_tool()
    engine = _engine(tmp_path / "cutoff.db")
    now = datetime.now(UTC)
    with Session(engine, future=True) as session:
        _refusal(session, refused_at=now)
        report = tool.QuarantineClearer(session, dry_run=False).clear(
            older_than=timedelta(hours=24),
            content_fingerprint=None,
            tenant_id=None,
            limit=10,
            actor_user_id=None,
            reason="too new",
        )
        assert report.selected == 0 and report.cleared == 0


# ------------------------------------------------- F-6: headers are not evidence
def test_the_append_header_is_not_a_segment():
    text_in = f"{REPORT}\nResident updates:\n水槽下方的存水弯也在渗水。"
    segments = [segment for segment, _ in rules._source_segments(text_in)]
    assert "Resident updates:" not in segments
    assert segments[0] == REPORT
    assert segments[-1] == "水槽下方的存水弯也在渗水。"
    assert len(segments) == 2


def test_a_header_alone_is_dropped_but_an_english_sentence_is_kept():
    assert rules._is_evidence_segment("Resident updates:") is False
    assert rules._is_evidence_segment("Attachments:") is False
    assert rules._is_evidence_segment("The pipe under the sink leaks.") is True
    assert rules._is_evidence_segment("2026-11-12") is True
    assert rules._source_segments("Resident updates:") == []


def test_the_catalog_no_longer_carries_a_zero_information_segment():
    catalog = rules.build_evidence_catalog(
        report_text=f"{REPORT}\nResident updates:\n水槽下方的存水弯也在渗水。",
        asr_text=None,
        vision_text="白色PVC排水管及弯头组件。",
    )
    texts = {evidence_id: str(item["text"]) for evidence_id, item in catalog.items()}
    assert "Resident updates:" not in texts.values()
    assert texts["report:1"] == REPORT
    assert texts["report:2"] == "水槽下方的存水弯也在渗水。"
    assert texts["vision:1"] == "白色PVC排水管及弯头组件。"
    # Evidence ids stay contiguous, so a bound observation still names a real segment.
    assert sorted(texts) == ["report:1", "report:2", "vision:1"]


def test_spans_still_point_into_the_original_report():
    text_in = f"{REPORT}\nResident updates:\n水槽下方的存水弯也在渗水。"
    segments = rules._source_segments(text_in)
    for segment, span in segments:
        assert text_in[span[0] : span[1]] == segment
