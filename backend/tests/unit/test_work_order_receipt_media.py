"""Receipt media phase and unresolved-receipt regression coverage."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from app.core.errors import AppError, WorkOrderErrorCode
from app.domain.work_order import MediaTakenPhase
from app.service.rbac import RbacService
from app.service.work_order import WorkOrderService


def test_public_receipt_contract_normalizes_legacy_after_media() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    expected = SimpleNamespace(id=41)
    service._validate_request_id = MagicMock()
    service._execute = MagicMock(side_effect=lambda operation: operation())
    service._submit_receipt = MagicMock(return_value=expected)

    result = service.submit_receipt(
        actor=MagicMock(),
        work_order_id=41,
        request_id="receipt-media-normalize",
        problem_type="PLUMBING",
        method="Replace valve",
        resolved=True,
        before_file_ids=(11, 11),
        after_file_ids=(12,),
        file_ids=(12, 13),
    )

    assert result is expected
    submitted = service._submit_receipt.call_args.kwargs
    assert submitted["before_file_ids"] == (11,)
    assert submitted["after_file_ids"] == (12, 13)


def test_receipt_rejects_same_file_in_before_and_after_phases() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._validate_request_id = MagicMock()
    service._execute = MagicMock()

    with pytest.raises(AppError) as captured:
        service.submit_receipt(
            actor=MagicMock(),
            work_order_id=41,
            request_id="receipt-media-overlap",
            problem_type="PLUMBING",
            method="Replace valve",
            resolved=True,
            before_file_ids=(11,),
            after_file_ids=(11,),
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_INPUT
    service._execute.assert_not_called()


@pytest.mark.parametrize("resolved", [True, False])
def test_receipt_attaches_both_media_phases_for_every_outcome(
    monkeypatch: pytest.MonkeyPatch,
    resolved: bool,
) -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = SimpleNamespace(
        id=41,
        tenant_id=2,
        property_id=3,
        reporter_user_id=8,
        status="PROCESSING",
        process_reason="NONE",
        resolved_at=None,
    )
    process = SimpleNamespace(id=9, ended_at=None)
    service._clock = MagicMock(return_value=datetime(2026, 9, 20, tzinfo=UTC))
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._property_scope = MagicMock()
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    service._repository.get_receipt_for_process.return_value = None
    service._attach_media = MagicMock()
    service._apply_transition = MagicMock()
    service._append_audit = MagicMock()
    service._notify = MagicMock()
    service._actor_type = MagicMock(return_value="STAFF")
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)

    result = service._submit_receipt(
        actor=MagicMock(),
        work_order_id=record.id,
        request_id=f"receipt-media-{resolved}",
        problem_type="PLUMBING",
        method="Replace valve",
        resolved=resolved,
        materials=None,
        fee_amount=None,
        fee_flag="NONE",
        unresolved_reason=None if resolved else "Needs specialist equipment",
        follow_up=None,
        before_file_ids=(11,),
        after_file_ids=(12,),
        version=1,
    )

    assert result is record
    assert service._attach_media.call_args_list == [
        call(
            record,
            actor=current,
            file_ids=(11,),
            taken_phase=MediaTakenPhase.BEFORE,
        ),
        call(
            record,
            actor=current,
            file_ids=(12,),
            taken_phase=MediaTakenPhase.AFTER,
        ),
    ]
    service._repository.add_receipt.assert_called_once()
    receipt = service._repository.add_receipt.call_args.args[0]
    assert bool(receipt.resolved) is resolved
    assert process.ended_at == datetime(2026, 9, 20, tzinfo=UTC)
