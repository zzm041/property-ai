"""t152 / PA-169 round 4: tenant scoping, punctuation semantics, dedup status, note."""

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import AiErrorCode
from app.domain.ai import content_fingerprint, normalize_fingerprint_text
from app.repository.ai import AiRepository
from app.repository.work_order import WorkOrderRepository
from app.service.analysis import AnalysisService
from app.service.analysis import orchestrator as orch
from app.service.work_order import WorkOrderService

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
REPORT_TEXT = "3号楼2单元楼道灯不亮"


# ---------------------------------------------------------------- F1: tenant
def _capture_read(**kwargs):
    statements = []

    class _Result:
        def first(self):
            return None

    class _Session:
        def execute(self, statement):
            statements.append(statement)
            return _Result()

    repository = AiRepository(_Session())
    assert repository.quarantined_output_for_content(**kwargs) is None
    return str(statements[0].compile(compile_kwargs={"literal_binds": False}))


def test_the_quarantine_read_is_scoped_to_the_tenant():
    compiled = _capture_read(
        content_fingerprint=content_fingerprint(REPORT_TEXT),
        tenant_id=5,
        property_id=5,
        error_codes=(AiErrorCode.INVALID_OUTPUT, AiErrorCode.OUTPUT_UNVERIFIABLE),
        since=datetime(2026, 11, 10, tzinfo=UTC),
    )

    assert "ai_call_logs.tenant_id" in compiled
    assert "ai_call_logs.property_id" in compiled
    # Still one bounded read, still keyed on content, never on the work order.
    assert "LIMIT" in compiled.upper()
    assert "ai_call_logs.content_fingerprint" in compiled
    assert "ai_call_logs.input_hash" not in compiled


def test_the_property_filter_is_optional_but_the_tenant_is_not():
    compiled = _capture_read(
        content_fingerprint=content_fingerprint(REPORT_TEXT),
        tenant_id=5,
        error_codes=(AiErrorCode.INVALID_OUTPUT,),
        since=datetime(2026, 11, 10, tzinfo=UTC),
    )
    assert "ai_call_logs.property_id" not in compiled

    with pytest.raises(ValueError, match="requires a tenant"):
        AiRepository(SimpleNamespace()).quarantined_output_for_content(
            content_fingerprint=content_fingerprint(REPORT_TEXT),
            tenant_id=0,
            error_codes=(AiErrorCode.INVALID_OUTPUT,),
            since=datetime(2026, 11, 10, tzinfo=UTC),
        )


def test_the_service_probe_carries_the_work_order_tenant_and_property():
    seen = {}
    service = object.__new__(AnalysisService)
    service._session = None
    service._clock = lambda: NOW
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **kwargs: seen.update(kwargs) or None
    )

    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    try:
        service._quarantine_for(
            SimpleNamespace(
                work_order_id=9,
                input_hash="a" * 64,
                tenant_id=7,
                property_id=8,
                text=REPORT_TEXT,
                images=(),
                audio=(),
            )
        )
    finally:
        orch.shared.begin_owned_transaction = original

    assert seen["tenant_id"] == 7
    assert seen["property_id"] == 8


# ------------------------------------------------------- F2: punctuation
def test_a_comma_never_merges_two_opposite_sentences():
    # "no repair needed" vs "doesn't need (this), repair it"
    assert normalize_fingerprint_text("不需要维修") != normalize_fingerprint_text(
        "不需要，维修"
    )
    assert normalize_fingerprint_text("不要动电闸") != normalize_fingerprint_text(
        "不要动，电闸"
    )


def test_the_negation_boundary_keeps_its_scope():
    assert normalize_fingerprint_text("不需要，维修") == normalize_fingerprint_text(
        "不需要。维修"
    )
    assert normalize_fingerprint_text("不需要，维修").endswith("|维修")
    assert normalize_fingerprint_text("不需要维修") == "不需要维修"


def test_punctuation_and_width_still_do_not_change_the_fingerprint():
    assert content_fingerprint("楼道灯不亮，看不清台阶") == content_fingerprint(
        "楼道灯不亮 看不清台阶"
    )
    assert content_fingerprint("ＡＢＣ楼道灯") == content_fingerprint("abc楼道灯")


def test_one_different_word_still_changes_the_fingerprint():
    assert content_fingerprint("楼道灯不亮") != content_fingerprint("楼道灯常亮")


# ------------------------------------------------------------- F3: dedup
def test_a_finished_report_is_not_offered_as_a_duplicate():
    statements = []

    class _Session:
        def scalar(self, statement):
            statements.append(statement)
            return None

    WorkOrderRepository(_Session()).find_duplicate_content(
        house_id=1,
        reporter_user_id=6,
        content_fingerprint=content_fingerprint(REPORT_TEXT),
        since=datetime(2026, 11, 11, 11, 50, tzinfo=UTC),
    )

    compiled = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "work_orders.status NOT IN" in compiled
    assert "CANCELLED" in compiled
    assert "CLOSED" in compiled


# ------------------------------------------------------------- F4: note
def test_the_quarantine_leaves_a_work_order_log_row():
    notes = []
    calls = {"providers": 0}
    service = object.__new__(AnalysisService)
    service._session = None
    service._force_retry_actor = None
    service._task_context = None
    service._clock = lambda: NOW
    service._llm = SimpleNamespace(model_version="synthetic-model")
    service._prepare = lambda **_: SimpleNamespace(
        work_order_id=11,
        input_hash="b" * 64,
        tenant_id=5,
        property_id=5,
        text=REPORT_TEXT,
        images=(),
        audio=(),
        skipped=False,
    )
    service._ai = SimpleNamespace(
        quarantined_output_for_content=lambda **_: (
            datetime(2026, 11, 11, 3, 0, tzinfo=UTC),
            3,
        ),
        get_current_analysis=lambda _id: None,
    )
    service._work_orders = SimpleNamespace(
        get_work_order=lambda _id: SimpleNamespace(
            id=11,
            tenant_id=5,
            property_id=5,
            status="PENDING_ANALYSIS",
            process_reason="NONE",
        ),
        add_log=lambda record: notes.append(record) or record,
    )
    service._write_call_log = lambda snapshot, **kwargs: None
    service._apply = lambda **kwargs: SimpleNamespace(status="FAILED")
    service._call_providers = lambda **kwargs: calls.__setitem__(
        "providers", calls["providers"] + 1
    )

    original = orch.shared.begin_owned_transaction
    orch.shared.begin_owned_transaction = lambda session: contextlib.nullcontext()
    try:
        service.analyze_work_order(work_order_id=11, request_id="req-152")
    finally:
        orch.shared.begin_owned_transaction = original

    assert calls["providers"] == 0
    assert len(notes) == 1
    record = notes[0]
    assert record.event_code == "AI_QUARANTINE"
    assert record.payload_json["reason_code"] == AiErrorCode.INPUT_QUARANTINED
    assert record.payload_json["previous_work_order_id"] == 3
    assert record.to_status == "PENDING_ANALYSIS"
    assert record.actor_type == "SYSTEM"


def test_the_supervisor_note_names_the_earlier_order():
    rows = [
        (11, {"reason_code": "AI.INPUT_QUARANTINED", "quarantined_since": "2026-11-11T04:00:00+00:00", "previous_work_order_id": 4}, NOW),
        (11, {"reason_code": "AI.INPUT_QUARANTINED", "quarantined_since": "2026-11-10T23:56:43+00:00", "previous_work_order_id": 3}, NOW),
        (13, {"reason_code": "AI.INPUT_QUARANTINED", "quarantined_since": None, "previous_work_order_id": None}, NOW),
    ]
    statements = []

    class _Result:
        def all(self):
            return rows

    class _Session:
        def execute(self, statement):
            statements.append(statement)
            return _Result()

    notes = WorkOrderRepository(_Session()).quarantine_notes([11, 12, 13])

    # The newest row per order wins, and orders without a note are absent.
    assert set(notes) == {11, 13}
    assert "工单 4" in notes[11]
    assert "2026-11-11T04:00:00+00:00" in notes[11]
    assert "工单 3" not in notes[11]
    # A row without a recorded origin still explains itself.
    assert "force_retry" in notes[13]
    assert "2026-11-11T12:00:00+00:00" in notes[13]

    compiled = str(statements[0].compile(compile_kwargs={"literal_binds": False}))
    assert "work_order_logs.event_code" in compiled
    assert "work_order_logs.work_order_id IN" in compiled

    # No ids, no query.
    empty = WorkOrderRepository(_Session())
    assert empty.quarantine_notes([]) == {}


def test_the_service_exposes_the_notes_for_the_queue():
    seen = {}

    class _Repository:
        def quarantine_notes(self, work_order_ids):
            seen["ids"] = tuple(work_order_ids)
            return {11: "note"}

    service = object.__new__(WorkOrderService)
    service._repository = _Repository()

    assert service.quarantine_notes([11, 12]) == {11: "note"}
    assert seen["ids"] == (11, 12)


def test_the_work_order_note_is_written_inside_an_owned_transaction(monkeypatch):
    """t152 acceptance found this: a bare ORM read left a transaction open and
    ``_apply`` then refused the request (TransactionContractError)."""

    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.service.analysis import context as shared

    entered = []

    def _begin(session):
        entered.append(session)
        return nullcontext()

    monkeypatch.setattr(shared, "begin_owned_transaction", _begin)

    service = object.__new__(AnalysisService)
    service._session = object()
    service._work_orders = SimpleNamespace(
        get_work_order=lambda _id: SimpleNamespace(
            id=205,
            tenant_id=5,
            property_id=5,
            status="PENDING_ANALYSIS",
            process_reason="NONE",
        ),
        add_log=lambda record: record,
    )

    service._record_quarantine_note(
        SimpleNamespace(work_order_id=205),
        request_id="req",
        quarantine={
            "content_fingerprint": "f" * 64,
            "quarantined_since": "2026-11-11T00:21:42+00:00",
            "previous_work_order_id": 163,
        },
    )
    assert entered, "the note must be committed in an owned transaction"


# ----------------------------------------------------- F5: schema surface
def test_the_create_schema_declares_deduplicated():
    from app.schema.work_order import CreateWorkOrderData, WorkOrderData

    assert "deduplicated" in CreateWorkOrderData.model_fields
    assert CreateWorkOrderData.model_fields["deduplicated"].default is False
    # The content fingerprint never reaches the API surface.
    assert "content_fingerprint" not in WorkOrderData.model_fields
