"""Resident work-order paging, append, cancel, rating, and page wiring."""

from datetime import UTC, datetime
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from app.api.v1.work_orders import get_work_order, list_my_work_orders
from app.core.errors import AppError, IamErrorCode, WorkOrderErrorCode
from app.core.errors import AiErrorCode
from app.domain.ai import AnalysisStatus
from app.domain.work_order import MediaTakenPhase, WorkOrderStatus
from app.service.analysis import AnalysisService, AnalysisSnapshot, ProviderBundle
from app.service.rbac import RbacService
from app.service.work_order import WorkOrderService


REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


def _record(record_id: int, status: str = "PENDING_ANALYSIS") -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        tenant_id=1,
        property_id=2,
        house_id=3,
        reporter_user_id=7,
        status=status,
        process_reason="NONE",
        category="OTHER",
        severity="LOW",
        priority="MEDIUM",
        version=4,
        current_assignment_id=None,
        incident_id=None,
        sla_due_at=None,
        assigned_at=None,
        accepted_at=None,
        started_at=None,
        resolved_at=None,
        closed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/work-orders",
            "headers": [],
        }
    )
    request.state.request_id = "wo012-unit"
    return request


def test_resident_list_uses_lookahead_and_exact_boundary_cursor() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._reload_actor = MagicMock(return_value=SimpleNamespace(user_id=7))
    service._repository = MagicMock()
    service._repository.list_for_reporter.return_value = tuple(
        _record(record_id) for record_id in range(1, 22)
    )

    records = service.list_my_work_orders(
        actor=MagicMock(),
        after_id=0,
        limit=20,
    )

    assert len(records) == 21
    service._repository.list_for_reporter.assert_called_once_with(
        user_id=7,
        after_id=0,
        limit=21,
    )

    route_service = MagicMock()
    route_service.is_overdue.return_value = False
    route_service.list_my_work_orders.return_value = records
    first_page = list_my_work_orders(
        _request(), MagicMock(), route_service, after_id=0, limit=20
    )["data"]
    assert [item["id"] for item in first_page["items"]] == list(range(1, 21))
    assert first_page["next_cursor"] == 20

    route_service.list_my_work_orders.return_value = records[:20]
    exact_final_page = list_my_work_orders(
        _request(), MagicMock(), route_service, after_id=0, limit=20
    )["data"]
    assert len(exact_final_page["items"]) == 20
    assert exact_final_page["next_cursor"] is None


def test_resident_append_rejects_non_report_phase_before_mutation() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = _record(41)
    content = SimpleNamespace(extra_notes="original")
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._authorize_read = MagicMock()
    service._repository = MagicMock()
    service._repository.get_content.return_value = content
    service._attach_media = MagicMock()

    with pytest.raises(AppError) as captured:
        service._append_content(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="wo012-append-phase",
            text="resident evidence",
            file_ids=(9,),
            taken_phase="AFTER",
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_INPUT
    assert content.extra_notes == "original"
    service._attach_media.assert_not_called()


def test_resident_append_persists_trimmed_note_and_report_media() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = _record(42)
    content = SimpleNamespace(extra_notes="first note")
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._authorize_read = MagicMock()
    service._repository = MagicMock()
    service._repository.get_content.return_value = content
    service._attach_media = MagicMock()
    service._write_log = MagicMock()
    service._session = MagicMock()

    result = service._append_content(
        actor=MagicMock(),
        work_order_id=record.id,
        request_id="wo012-append",
        text="  second note  ",
        file_ids=(9,),
        taken_phase="REPORT",
    )

    assert result is record
    assert content.extra_notes == "first note\nsecond note"
    assert record.version == 5
    service._attach_media.assert_called_once_with(
        record,
        actor=current,
        file_ids=(9,),
        taken_phase=MediaTakenPhase.REPORT,
    )


def test_pending_append_enqueues_fresh_analysis_after_commit() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    pending = _record(43)
    service._validate_request_id = MagicMock()
    service._execute = MagicMock(return_value=pending)
    service._enqueue_analysis = MagicMock()

    assert (
        service.append_content(
            actor=MagicMock(),
            work_order_id=pending.id,
            request_id="wo012-append-reanalyze",
            text="new safety detail",
        )
        is pending
    )
    service._enqueue_analysis.assert_called_once_with(
        pending.id, "wo012-append-reanalyze"
    )

    pending.status = WorkOrderStatus.PENDING_ASSIGNMENT.value
    service._enqueue_analysis.reset_mock()
    service.append_content(
        actor=MagicMock(),
        work_order_id=pending.id,
        request_id="wo012-append-no-reanalyze",
        text="visible to assigned staff",
    )
    service._enqueue_analysis.assert_not_called()


def test_resident_append_rejects_stale_version_before_mutation() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = _record(44)
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._authorize_read = MagicMock()
    service._repository = MagicMock()
    service._attach_media = MagicMock()

    with pytest.raises(AppError) as captured:
        service._append_content(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="wo012-append-stale",
            text="duplicate click",
            file_ids=(),
            taken_phase="REPORT",
            version=record.version - 1,
        )

    assert captured.value.code == WorkOrderErrorCode.CONFLICT
    service._authorize_read.assert_not_called()
    service._repository.get_content.assert_not_called()
    service._attach_media.assert_not_called()


def test_cancel_rejects_other_resident_even_with_resident_cancel_permission() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = MagicMock()
    current.user_id = 8
    current.has_permission.side_effect = (
        lambda permission, _scope: permission in {"work_order:read", "work_order:cancel"}
    )
    record = _record(45, WorkOrderStatus.PENDING_ASSIGNMENT.value)
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._assert_version = MagicMock()
    service._repository = MagicMock()
    service._repository.get_active_assignment.return_value = None
    service._employees = MagicMock()
    service._employees.get_employee_for_user.return_value = None
    service._apply_transition = MagicMock()

    with pytest.raises(AppError) as captured:
        service._cancel(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="wo012-cancel-other-resident",
            reason="not mine",
            version=record.version,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._apply_transition.assert_not_called()


def test_changed_pending_content_creates_new_analysis_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.service.analysis.begin_owned_transaction",
        lambda _session: nullcontext(),
    )
    service = AnalysisService.__new__(AnalysisService)
    service._session = MagicMock()
    service._work_orders = MagicMock()
    service._ai = MagicMock()
    record = _record(44)
    content = SimpleNamespace(
        text="water leak",
        extra_notes="water is near an outlet",
        asr_text=None,
    )
    current = SimpleNamespace(
        id=71,
        analysis_version=1,
        status=AnalysisStatus.PROCESSING.value,
        input_hash="old-input",
        error_code=None,
    )
    service._work_orders.get_work_order_for_update.return_value = record
    service._work_orders.get_content.return_value = content
    service._ai.list_media_with_files.return_value = ()
    service._ai.get_current_analysis_for_update.return_value = current
    service._ai.get_building_id.return_value = 9

    def add_analysis(model):
        model.id = 72
        return model

    service._ai.add_analysis.side_effect = add_analysis

    snapshot = service._prepare(
        work_order_id=record.id,
        request_id="wo012-analysis-refresh",
    )

    assert snapshot is not None
    assert snapshot.analysis_id == 72
    assert snapshot.analysis_version == 2
    assert "water leak" in snapshot.text
    assert "water is near an outlet" in snapshot.text
    assert snapshot.input_hash != "old-input"
    assert current.status == AnalysisStatus.STALE.value
    assert current.error_code == AiErrorCode.STALE_RESULT


def test_analysis_apply_rejects_snapshot_from_before_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.service.analysis.begin_owned_transaction",
        lambda _session: nullcontext(),
    )
    service = AnalysisService.__new__(AnalysisService)
    service._session = MagicMock()
    service._work_orders = MagicMock()
    service._ai = MagicMock()
    service._append_failure_audit = MagicMock()
    service._validate_payload = MagicMock(return_value=(MagicMock(), None))
    record = _record(45)
    record.version = 6
    analysis = SimpleNamespace(
        id=81,
        status=AnalysisStatus.PROCESSING.value,
        input_hash="before-append",
        error_code=None,
    )
    service._work_orders.get_work_order_for_update.return_value = record
    service._work_orders.get_content_for_update.return_value = None
    service._ai.get_current_analysis_for_update.return_value = analysis
    snapshot = AnalysisSnapshot(
        work_order_id=record.id,
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        house_id=record.house_id,
        building_id=None,
        analysis_id=analysis.id,
        analysis_version=1,
        work_order_version=5,
        input_hash="before-append",
        text="old detail",
        existing_asr_text=None,
        images=(),
        audio=(),
    )

    result = service._apply(
        snapshot=snapshot,
        bundle=ProviderBundle(
            asr_text=None,
            vision_text=None,
            llm_payload={},
            error_code=None,
            call_status="SUCCESS",
        ),
        request_id="wo012-analysis-stale",
        attempt=1,
    )

    assert result is analysis
    assert analysis.status == AnalysisStatus.STALE.value
    assert analysis.error_code == AiErrorCode.STALE_RESULT
    service._append_failure_audit.assert_called_once_with(
        record,
        analysis,
        "wo012-analysis-stale",
        AiErrorCode.STALE_RESULT,
    )


def test_resident_cancel_accepts_unassigned_states_and_preserves_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)
    for status in ("PENDING_ANALYSIS", "PENDING_ASSIGNMENT"):
        service = WorkOrderService.__new__(WorkOrderService)
        current = SimpleNamespace(user_id=7)
        record = _record(50, status)
        service._reload_actor = MagicMock(return_value=current)
        service._lock_work_order = MagicMock(return_value=record)
        service._assert_version = MagicMock()
        service._property_scope = MagicMock()
        service._actor_type = MagicMock(return_value="RESIDENT")
        service._apply_transition = MagicMock()
        service._stop_sla = MagicMock()
        service._append_audit = MagicMock()

        assert (
            service._cancel(
                actor=MagicMock(),
                work_order_id=record.id,
                request_id=f"wo012-cancel-{status}",
                reason="  duplicate report  ",
                version=record.version,
            )
            is record
        )
        assert service._apply_transition.call_args.kwargs["reason_detail"] == (
            "duplicate report"
        )
        service._assert_version.assert_called_once_with(record, record.version)


def test_resident_cancel_rejects_assigned_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = _record(51, "ASSIGNED")
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._assert_version = MagicMock()
    service._property_scope = MagicMock()
    service._apply_transition = MagicMock()

    with pytest.raises(AppError) as captured:
        service._cancel(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="wo012-cancel-assigned",
            reason=None,
            version=record.version,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._apply_transition.assert_not_called()


def test_rating_is_single_submission_under_locked_work_order() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = _record(60, "CLOSED")
    service._reload_actor = MagicMock(return_value=current)
    service._lock_work_order = MagicMock(return_value=record)
    service._require_reporter = MagicMock()
    service._repository = MagicMock()
    service._repository.get_rating_for_update.return_value = SimpleNamespace(id=3)
    service._write_log = MagicMock()

    with pytest.raises(AppError) as captured:
        service._rate(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="wo012-rate-repeat",
            rating=5,
            comment="again",
        )

    assert captured.value.code == WorkOrderErrorCode.ALREADY_PROCESSED
    service._repository.add_feedback.assert_not_called()
    service._write_log.assert_not_called()


def test_detail_returns_saved_rating_and_cancel_reason() -> None:
    record = _record(61, "CLOSED")
    feedback = SimpleNamespace(
        id=4,
        process_id=None,
        actor_user_id=7,
        confirmed=None,
        rating=5,
        comment="resolved well",
        unresolved_flag=0,
        created_at=NOW,
    )
    log = SimpleNamespace(
        id=8,
        actor_user_id=7,
        actor_type="RESIDENT",
        from_status="PENDING_ANALYSIS",
        to_status="CANCELLED",
        process_reason="NONE",
        event_code="CANCEL",
        payload_json={"event_code": "CANCEL", "reason": "duplicate report"},
        created_at=NOW,
    )
    service = MagicMock()
    service.is_overdue.return_value = False
    service.get_work_order_detail.return_value = (
        record,
        SimpleNamespace(text="original", asr_text=None, extra_notes="more"),
        (),
        (),
        (),
        (),
        (feedback,),
        (log,),
    )

    detail = get_work_order(61, _request(), MagicMock(), service)["data"]

    assert detail["feedbacks"] == [
        {
            "id": 4,
            "process_id": None,
            "actor_user_id": 7,
            "confirmed": None,
            "rating": 5,
            "comment": "resolved well",
            "unresolved_flag": False,
            "created_at": "2026-09-22T08:00:00Z",
        }
    ]
    assert detail["logs"][0]["reason"] == "duplicate report"


def test_miniprogram_wires_real_paging_and_resident_actions() -> None:
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    listing = (
        REPO_ROOT / "miniprogram/pages/work-orders/list.js"
    ).read_text(encoding="utf-8")
    list_template = (
        REPO_ROOT / "miniprogram/pages/work-orders/list.wxml"
    ).read_text(encoding="utf-8")
    detail = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.js"
    ).read_text(encoding="utf-8")
    detail_template = (
        REPO_ROOT / "miniprogram/pages/work-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    assert "listWorkOrders: (afterId, limit = 20)" in api
    assert "after_id=${afterId}" in api
    assert "mergeById" in listing
    assert "Number(nextCursor) <= Number(afterId || 0)" in listing
    assert 'data-action="load-more"' in list_template

    for method in ("appendWorkOrder", "cancelWorkOrder", "rateWorkOrder"):
        assert method in api
        assert method in detail
    for action in ("append", "cancel", "rate"):
        assert f'data-action="{action}"' in detail_template
    assert "item.extra_notes" in detail_template
    assert "latestRating" in detail
    assert "latestRating.rating" in detail_template
    assert "version: this.data.workOrder.version" in detail
    assert 'taken_phase: "REPORT"' in detail
