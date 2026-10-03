"""Receipt media phase and unresolved-receipt regression coverage."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, WorkOrderErrorCode
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.work_order import (
    MediaTakenPhase,
    WorkOrderActorType,
    WorkOrderEvent,
    WorkOrderStatus,
)
from app.schema.work_order import StaffReceiptMediaRequest
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


def test_public_receipt_media_supplement_requires_files_and_normalizes_ids() -> None:
    """Removing input guards or de-duplication must make this fail."""

    service = WorkOrderService.__new__(WorkOrderService)
    expected = SimpleNamespace(id=41)
    service._validate_request_id = MagicMock()
    service._execute = MagicMock(side_effect=lambda operation: operation())
    service._append_receipt_media = MagicMock(return_value=expected)

    result = service.append_receipt_media(
        actor=MagicMock(),
        work_order_id=41,
        receipt_id=77,
        request_id="receipt-media-supplement-public",
        before_file_ids=(11, 11),
        after_file_ids=(12, 12),
        version=5,
    )

    assert result is expected
    submitted = service._append_receipt_media.call_args.kwargs
    assert submitted["before_file_ids"] == (11,)
    assert submitted["after_file_ids"] == (12,)

    with pytest.raises(AppError) as captured:
        service.append_receipt_media(
            actor=MagicMock(),
            work_order_id=41,
            receipt_id=77,
            request_id="receipt-media-supplement-empty",
            before_file_ids=(),
            after_file_ids=(),
            version=5,
        )
    assert captured.value.code == WorkOrderErrorCode.CONTENT_REQUIRED


def test_receipt_media_supplement_schema_requires_optimistic_version() -> None:
    """Omitting version must not bypass the documented conflict guard."""

    with pytest.raises(ValidationError):
        StaffReceiptMediaRequest.model_validate({"before_file_ids": [11]})

    parsed = StaffReceiptMediaRequest.model_validate(
        {"before_file_ids": [11], "version": 5}
    )
    assert parsed.version == 5


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
    process = SimpleNamespace(
        id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=3,
        ended_at=None,
    )
    service._clock = MagicMock(return_value=datetime(2026, 9, 20, tzinfo=UTC))
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._property_scope = MagicMock()
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    saved_receipt: dict[str, object] = {}
    service._repository.get_receipt_for_process.side_effect = (
        lambda _process_id: saved_receipt.get("receipt")
    )

    def add_receipt(receipt):
        receipt.id = 77
        saved_receipt["receipt"] = receipt
        return receipt

    service._repository.add_receipt.side_effect = add_receipt
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
            process_id=process.id,
            receipt_id=77,
        ),
        call(
            record,
            actor=current,
            file_ids=(12,),
            taken_phase=MediaTakenPhase.AFTER,
            process_id=process.id,
            receipt_id=77,
        ),
    ]
    service._repository.add_receipt.assert_called_once()
    receipt = service._repository.add_receipt.call_args.args[0]
    assert bool(receipt.resolved) is resolved
    assert process.ended_at == datetime(2026, 9, 20, tzinfo=UTC)


def test_staff_on_site_media_is_bound_to_current_process(
    monkeypatch: pytest.MonkeyPatch,
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
    )
    process = SimpleNamespace(
        id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=3,
    )
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    service._attach_media = MagicMock()
    service._apply_transition = MagicMock()
    service._append_audit = MagicMock()
    service._actor_type = MagicMock(return_value="STAFF")
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)

    result = service._feedback(
        actor=MagicMock(),
        work_order_id=record.id,
        request_id="on-site-cycle",
        text=None,
        process_reason=None,
        file_ids=(13,),
        taken_phase="ON_SITE",
        version=1,
    )

    assert result is record
    service._attach_media.assert_called_once_with(
        record,
        actor=current,
        file_ids=(13,),
        taken_phase=MediaTakenPhase.ON_SITE,
        process_id=process.id,
    )


def test_staff_on_site_media_fails_closed_without_current_process() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = SimpleNamespace(
        id=41,
        tenant_id=2,
        property_id=3,
        reporter_user_id=8,
        status="PROCESSING",
        process_reason="NONE",
    )
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = None

    with pytest.raises(AppError) as captured:
        service._feedback(
            actor=MagicMock(),
            work_order_id=record.id,
            request_id="on-site-no-cycle",
            text=None,
            process_reason=None,
            file_ids=(13,),
            taken_phase="ON_SITE",
            version=1,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._repository.add_media.assert_not_called()


def test_receipt_media_scope_is_verified_before_file_attachment() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    record = SimpleNamespace(id=41, tenant_id=2, property_id=3)
    process = SimpleNamespace(
        id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=3,
    )
    wrong_receipt = SimpleNamespace(
        id=77,
        process_id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=4,
    )
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    service._repository.get_receipt_for_process.return_value = wrong_receipt

    with pytest.raises(AppError) as captured:
        service._attach_media(
            record,
            actor=SimpleNamespace(user_id=7),
            file_ids=(11,),
            taken_phase=MediaTakenPhase.AFTER,
            process_id=9,
            receipt_id=77,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._repository.get_file.assert_not_called()
    service._repository.add_media.assert_not_called()


def test_staff_can_supplement_media_for_current_receipt_after_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the WAIT_CONFIRM supplement path must make this fail."""

    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = SimpleNamespace(
        id=41,
        tenant_id=2,
        property_id=3,
        status="WAIT_CONFIRM",
        process_reason="NONE",
        version=5,
    )
    process = SimpleNamespace(
        id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=3,
    )
    receipt = SimpleNamespace(
        id=77,
        process_id=9,
        work_order_id=41,
        tenant_id=2,
        property_id=3,
    )
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._property_scope = MagicMock()
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    service._repository.get_receipt_for_process.return_value = receipt
    service._attach_media = MagicMock()
    service._write_log = MagicMock()
    service._append_audit = MagicMock()
    service._session = MagicMock()
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)

    actor = MagicMock()
    result = service._append_receipt_media(
        actor=actor,
        work_order_id=record.id,
        receipt_id=receipt.id,
        request_id="receipt-media-supplement",
        before_file_ids=(11,),
        after_file_ids=(12,),
        version=5,
    )

    assert result is record
    assert record.version == 6
    service._lock_assignee_action.assert_called_once_with(
        actor,
        record.id,
        5,
        takeover=False,
    )
    assert service._attach_media.call_args_list == [
        call(
            record,
            actor=current,
            file_ids=(11,),
            taken_phase=MediaTakenPhase.BEFORE,
            process_id=process.id,
            receipt_id=receipt.id,
        ),
        call(
            record,
            actor=current,
            file_ids=(12,),
            taken_phase=MediaTakenPhase.AFTER,
            process_id=process.id,
            receipt_id=receipt.id,
        ),
    ]
    service._write_log.assert_called_once_with(
        record,
        actor=current,
        actor_type=WorkOrderActorType.STAFF,
        from_status=WorkOrderStatus.WAIT_CONFIRM,
        to_status=WorkOrderStatus.WAIT_CONFIRM,
        event=WorkOrderEvent.RECEIPT_MEDIA_APPENDED,
        request_id="receipt-media-supplement",
        reason_code="RECEIPT_MEDIA_APPENDED",
    )
    service._append_audit.assert_called_once_with(
        action=AuditAction.WORK_ORDER_RECEIPT_MEDIA_APPENDED,
        resource_type=AuditResourceType.WORK_ORDER,
        actor_user_id=current.user_id,
        resource_id=record.id,
        request_id="receipt-media-supplement",
        tenant_id=record.tenant_id,
        property_id=record.property_id,
        details={
            "work_order_id": record.id,
            "receipt_id": receipt.id,
            "process_id": process.id,
            "before_count": 1,
            "after_count": 1,
        },
    )
    service._session.flush.assert_called_once()


def test_receipt_media_supplement_rejects_non_wait_confirm_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed receipt is not enough unless the order awaits confirmation."""

    service = WorkOrderService.__new__(WorkOrderService)
    record = SimpleNamespace(
        id=41,
        tenant_id=2,
        property_id=3,
        status="PROCESSING",
        process_reason="NONE",
        version=5,
    )
    service._lock_assignee_action = MagicMock(
        return_value=(SimpleNamespace(user_id=7), record, MagicMock(), MagicMock())
    )
    service._property_scope = MagicMock()
    service._repository = MagicMock()
    service._attach_media = MagicMock()
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)

    with pytest.raises(AppError) as captured:
        service._append_receipt_media(
            actor=MagicMock(),
            work_order_id=record.id,
            receipt_id=77,
            request_id="receipt-media-wrong-state",
            before_file_ids=(11,),
            after_file_ids=(),
            version=5,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._repository.get_current_process.assert_not_called()
    service._attach_media.assert_not_called()


def test_receipt_media_supplement_rejects_non_current_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt from another cycle must never receive the new files."""

    service = WorkOrderService.__new__(WorkOrderService)
    current = SimpleNamespace(user_id=7)
    record = SimpleNamespace(
        id=41,
        tenant_id=2,
        property_id=3,
        status="WAIT_CONFIRM",
        process_reason="NONE",
        version=5,
    )
    process = SimpleNamespace(id=9)
    receipt = SimpleNamespace(id=78)
    service._lock_assignee_action = MagicMock(
        return_value=(current, record, MagicMock(), MagicMock())
    )
    service._property_scope = MagicMock()
    service._repository = MagicMock()
    service._repository.get_current_process.return_value = process
    service._repository.get_receipt_for_process.return_value = receipt
    service._attach_media = MagicMock()
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)

    with pytest.raises(AppError) as captured:
        service._append_receipt_media(
            actor=MagicMock(),
            work_order_id=record.id,
            receipt_id=77,
            request_id="receipt-media-wrong-cycle",
            before_file_ids=(11,),
            after_file_ids=(),
            version=5,
        )

    assert captured.value.code == WorkOrderErrorCode.INVALID_TRANSITION
    service._attach_media.assert_not_called()
