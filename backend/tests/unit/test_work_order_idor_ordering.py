"""Anti-IDOR ordering for optimistic work-order mutations.

An actor who cannot operate on a work order must not be able to distinguish a
current version from a stale one.  Authorization therefore precedes optimistic
version checks, while the locked row still protects the authorized mutation.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, IamErrorCode, WorkOrderErrorCode
from app.service.identity.rbac import RbacService
from app.service.work_order import WorkOrderService


def _denied() -> AppError:
    return AppError(
        code=IamErrorCode.FORBIDDEN,
        message="Permission denied",
        status_code=403,
    )


def _service(*, reporter_user_id: int = 7) -> tuple[WorkOrderService, object, object]:
    service = WorkOrderService.__new__(WorkOrderService)
    actor = MagicMock(user_id=8)
    actor.has_permission.return_value = False
    record = SimpleNamespace(
        id=41,
        tenant_id=1,
        property_id=2,
        reporter_user_id=reporter_user_id,
        version=4,
        status="PENDING_ASSIGNMENT",
    )
    service._reload_actor = MagicMock(return_value=actor)
    service._lock_work_order = MagicMock(return_value=record)
    service._property_scope = MagicMock(return_value=MagicMock())
    service._assert_version = MagicMock(
        side_effect=AppError(
            code=WorkOrderErrorCode.CONFLICT,
            message="stale",
            status_code=409,
        )
    )
    service._repository = MagicMock()
    service._employees = MagicMock()
    return service, actor, record


def test_unauthorized_append_hides_stale_version() -> None:
    service, actor, record = _service()
    service._authorize_read = MagicMock(side_effect=_denied())

    with pytest.raises(AppError) as captured:
        service._append_content(
            actor=actor,
            work_order_id=record.id,
            request_id="idor-append",
            text="not mine",
            file_ids=(),
            taken_phase="REPORT",
            version=999,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._assert_version.assert_not_called()


def test_unauthorized_cancel_hides_stale_version() -> None:
    service, actor, record = _service()

    with pytest.raises(AppError) as captured:
        service._cancel(
            actor=actor,
            work_order_id=record.id,
            request_id="idor-cancel",
            reason="not mine",
            version=999,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._assert_version.assert_not_called()


@pytest.mark.parametrize("operation", ["confirm", "reopen"])
def test_unauthorized_reporter_action_hides_stale_version(operation: str) -> None:
    service, actor, record = _service()
    service._require_reporter = MagicMock(side_effect=_denied())

    with pytest.raises(AppError) as captured:
        if operation == "confirm":
            service._confirm(
                actor=actor,
                work_order_id=record.id,
                request_id="idor-confirm",
                version=999,
            )
        else:
            service._reopen(
                actor=actor,
                work_order_id=record.id,
                request_id="idor-reopen",
                reason="not mine",
                version=999,
            )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._assert_version.assert_not_called()


@pytest.mark.parametrize("operation", ["assign", "decide"])
def test_unauthorized_manager_action_hides_stale_version(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    service, actor, record = _service()
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: (_ for _ in ()).throw(_denied()))

    with pytest.raises(AppError) as captured:
        if operation == "assign":
            service._assign(
                actor=actor,
                work_order_id=record.id,
                employee_id=11,
                request_id="idor-assign",
                reason=None,
                version=999,
                claimed_tenant_id=record.tenant_id,
                claimed_property_id=record.property_id,
            )
        else:
            service._decide(
                actor=actor,
                work_order_id=record.id,
                decision="CONTINUE",
                request_id="idor-decide",
                employee_id=None,
                reason="continue",
                version=999,
            )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._assert_version.assert_not_called()


def test_unrelated_staff_action_hides_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, actor, record = _service()
    monkeypatch.setattr(RbacService, "require_permission", lambda *_args: None)
    service._repository.get_active_assignment_for_update.return_value = SimpleNamespace(
        employee_id=11
    )
    service._require_actor_employee = MagicMock(return_value=SimpleNamespace(id=12))

    with pytest.raises(AppError) as captured:
        service._lock_assignee_action(
            actor,
            record.id,
            999,
            takeover=False,
        )

    assert captured.value.code == WorkOrderErrorCode.NOT_ASSIGNEE
    service._assert_version.assert_not_called()
