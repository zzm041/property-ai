"""Resident/property projections and historical attachment access, synthetic only."""
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from app.api.v1.work_orders import get_work_order
from app.core.errors import AppError
from app.domain.work_order import WORK_ORDER_READ
from app.domain.work_order import MediaTakenPhase
from app.service.work_order import WorkOrderService

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def order():
    return NS(id=41, tenant_id=4, property_id=9, house_id=10, reporter_user_id=7,
              status="PROCESSING", process_reason="EXTERNAL_COORDINATION", category="PLUMBING",
              severity="HIGH", priority="HIGH", version=2, current_assignment_id=3, incident_id=19,
              sla_due_at=NOW, assigned_at=NOW, accepted_at=NOW, started_at=NOW,
              resolved_at=None, closed_at=None, created_at=NOW, updated_at=NOW)


def media(file_id, phase, receipt_id=None):
    return NS(id=file_id, file_id=file_id, taken_phase=phase, receipt_id=receipt_id,
              process_id=1, uploader_user_id=8, kind="IMAGE", created_at=NOW)


def log(event, actor_type="STAFF", user=8, reason="internal-private"):
    return NS(id=1, actor_user_id=user, actor_type=actor_type, from_status="PROCESSING",
              to_status="PROCESSING", process_reason="EXTERNAL_COORDINATION", event_code=event,
              payload_json={"reason": reason}, created_at=NOW)


def payload_context():
    record = order()
    content = NS(text="report", asr_text=None, extra_notes="mixed-internal-private")
    receipts = (NS(id=5, process_id=1, problem_type="PLUMBING", method="repair",
                   materials="seal", fee_amount=25, fee_flag="RECORDED", resolved=0,
                   unresolved_reason="still leaking", follow_up="next visit", finished_at=NOW),)
    logs = (log("ESCALATE"), log("AI_LLM_CHECKPOINT"),
            log("APPEND", "RESIDENT", 7, "my supplement"))
    return (record, content, (media(21, "REPORT"), media(22, "ON_SITE"),
                             media(23, "AFTER", 5), media(24, "AFTER", 999)),
            (NS(id=3, employee_id=17, assigned_by_user_id=8, status="ACTIVE",
                assigned_at=NOW, accept_at=NOW, ended_at=None, reason="dispatch-private"),),
            (NS(id=1, cycle_no=1, assignee_id=17, started_at=NOW, ended_at=None),),
            receipts, (), logs, {"property_timezone": "Asia/Shanghai", "assignee_names": {17: "technician"}})


def test_default_detail_is_resident_safe_even_for_dual_role_actor():
    service = MagicMock()
    raw = payload_context()
    service.get_work_order_detail.return_value = raw
    service.is_overdue.return_value = True
    request = Request({"type": "http", "headers": []})
    data = get_work_order(41, request, MagicMock(user_id=7), service)["data"]
    assert "private" not in str(data)
    assert data["extra_notes"] == "my supplement"
    assert [m["file_id"] for m in data["media"]] == [21, 23]
    assert all("uploader_user_id" not in m for m in data["media"])
    assert all("assigned_by_user_id" not in a and "reason" not in a for a in data["assignments"])
    assert "AI_LLM_CHECKPOINT" not in str(data)
    assert "incident_id" not in data["work_order"]
    assert data["work_order"]["process_reason"] == "EXTERNAL_COORDINATION"
    assert data["receipts"][0]["unresolved_reason"] == "still leaking"
    assert data["receipts"][0]["fee_amount"] == "25"
    assert raw[1].extra_notes == "mixed-internal-private"


def test_explicit_property_view_preserves_internal_data_and_forwards_authorization_mode():
    service = MagicMock()
    service.get_work_order_detail.return_value = payload_context()
    service.is_overdue.return_value = False
    request = Request({"type": "http", "headers": []})
    actor = MagicMock(user_id=8)
    data = get_work_order(41, request, actor, service, audience="property")["data"]
    assert data["extra_notes"] == "mixed-internal-private"
    assert len(data["media"]) == 4
    service.get_work_order_detail.assert_called_once_with(actor=actor, work_order_id=41, audience="property")


def test_resident_keeps_safe_auto_dispatch_marker_without_private_assignment_reason():
    service = MagicMock()
    raw = payload_context()
    raw[3][0].reason = "AUTO_DISPATCH"
    raw[3][0].assigned_by_user_id = None
    service.get_work_order_detail.return_value = raw
    service.is_overdue.return_value = False
    data = get_work_order(41, Request({"type": "http", "headers": []}), MagicMock(), service)["data"]
    assert data["assignments"][0]["reason"] == "AUTO_DISPATCH"
    assert "assigned_by_user_id" not in data["assignments"][0]


@pytest.fixture
def access():
    s = WorkOrderService.__new__(WorkOrderService)
    s._clock = lambda: NOW
    s._repository = MagicMock()
    s._employees = MagicMock()
    s._analysis = MagicMock()
    actor = MagicMock(user_id=7)
    actor.has_permission.side_effect = lambda permission, scope: permission == WORK_ORDER_READ
    s._reload_actor = MagicMock(return_value=actor)
    s._repository.get_work_order.return_value = order()
    s._repository.get_file.return_value = NS(id=22, uploader_user_id=7)
    s._repository.list_work_orders_for_file.return_value = (order(),)
    s._repository.list_media.return_value = (media(22, "ON_SITE"),)
    s._repository.list_receipts.return_value = (NS(id=5),)
    s._read_stored_file = MagicMock(return_value=b"synthetic")
    s._employees.get_employee_for_user.return_value = None
    return s, actor


def test_reporter_cannot_request_property_view_without_staff_authority(access):
    s, actor = access
    with pytest.raises(AppError) as error:
        s.get_work_order_detail(actor=actor, work_order_id=41, audience="property")
    assert error.value.status_code == 403
    s._repository.get_content.assert_not_called()


@pytest.mark.parametrize("phase,receipt_id", [("ON_SITE", None), ("AFTER", None), ("AFTER", 999)])
def test_resident_cannot_download_internal_or_unlinked_receipt_media_even_if_uploader(access, phase, receipt_id):
    s, actor = access
    s._repository.list_media.return_value = (media(22, phase, receipt_id),)
    with pytest.raises(AppError) as error:
        s.get_file_content(actor=actor, file_id=22)
    assert error.value.status_code == 404
    s._read_stored_file.assert_not_called()


@pytest.mark.parametrize("phase,receipt_id", [("REPORT", None), ("BEFORE", 5), ("AFTER", 5)])
def test_resident_can_download_report_and_receipt_history(access, phase, receipt_id):
    s, actor = access
    s._repository.list_media.return_value = (media(22, phase, receipt_id),)
    assert s.get_file_content(actor=actor, file_id=22)[1] == b"synthetic"


def test_historical_uploader_has_no_bypass_when_no_longer_authorized(access):
    s, actor = access
    actor.user_id = 8
    s._repository.get_file.return_value.uploader_user_id = 8
    with pytest.raises(AppError) as error:
        s.get_file_content(actor=actor, file_id=22)
    assert error.value.status_code == 404
    s._read_stored_file.assert_not_called()


def test_unattached_own_upload_can_still_be_previewed(access):
    s, actor = access
    s._repository.list_work_orders_for_file.return_value = ()
    assert s.get_file_content(actor=actor, file_id=22)[1] == b"synthetic"


def test_property_file_view_requires_live_authority_and_allows_internal_media(access):
    s, actor = access
    with pytest.raises(AppError):
        s.get_file_content(actor=actor, file_id=22, audience="property")
    actor.has_permission.side_effect = lambda *_: True
    assert s.get_file_content(actor=actor, file_id=22, audience="property")[1] == b"synthetic"


@pytest.mark.parametrize("event", ["DECIDE", "ESCALATE", "REASSIGN"])
def test_dual_role_reporter_internal_reason_is_not_public(event):
    service = MagicMock()
    raw = list(payload_context())
    raw[7] = (log(event, "RESIDENT", 7, "private-manager-note"),)
    service.get_work_order_detail.return_value = tuple(raw)
    service.is_overdue.return_value = False
    data = get_work_order(41, Request({"type": "http", "headers": []}), MagicMock(), service)["data"]
    assert "private-manager-note" not in str(data)


@pytest.mark.parametrize("retry", [False, True])
def test_resident_sees_failure_and_retry_lifecycle_without_internal_payload(retry):
    service = MagicMock()
    raw = list(payload_context())
    failed = log("AI_TASK_FAILED", "SYSTEM", None)
    failed.id = 20
    attempted = log("AI_TASK_ATTEMPT", "SYSTEM", None)
    attempted.id = 21
    raw[7] = (failed, attempted) if retry else (failed,)
    service.get_work_order_detail.return_value = tuple(raw)
    service.is_overdue.return_value = False
    data = get_work_order(41, Request({"type": "http", "headers": []}), MagicMock(), service)["data"]
    assert data["logs"][-1]["event_code"] == ("AI_TASK_ATTEMPT" if retry else "AI_TASK_FAILED")
    assert "internal-private" not in str(data)


@pytest.mark.parametrize("dual_role", [False, True])
def test_rebinding_cannot_launder_inaccessible_history_into_resident_report(access, dual_role):
    s, actor = access
    actor.user_id = 8
    if dual_role:
        actor.has_permission.side_effect = lambda *_: True
    s._repository.get_file.return_value = NS(id=22, tenant_id=4, property_id=9, uploader_user_id=8, mime_type="image/jpeg")
    target = order()
    target.id, target.reporter_user_id = 42, 8
    with pytest.raises(AppError) as error:
        s._attach_media(target, actor=actor, file_ids=(22,), taken_phase=MediaTakenPhase.REPORT)
    assert error.value.status_code == 404
    s._repository.add_media.assert_not_called()
    s._read_stored_file.assert_not_called()


def test_rebinding_public_authorized_report_is_still_possible(access):
    s, actor = access
    s._repository.get_file.return_value = NS(id=22, tenant_id=4, property_id=9, uploader_user_id=7, mime_type="image/jpeg")
    s._repository.list_media.return_value = (media(22, "REPORT"),)
    target = order()
    target.id = 42
    s._attach_media(target, actor=actor, file_ids=(22,), taken_phase=MediaTakenPhase.REPORT)
    assert s._repository.add_media.call_args.args[0].work_order_id == 42


def test_new_resident_supplement_saves_recoverable_source(access):
    s, actor = access
    s._session = MagicMock()
    s._lock_work_order = MagicMock(return_value=order())
    s._repository.get_content.return_value = NS(extra_notes="old-private-mixed")
    s._attach_media = MagicMock()
    s._append_content(actor=actor, work_order_id=41, request_id="synthetic-append",
                      text="my new supplement", file_ids=(), taken_phase="REPORT")
    saved = s._repository.add_log.call_args.args[0]
    assert saved.actor_user_id == 7 and saved.actor_type == "RESIDENT" and saved.event_code == "APPEND"
    assert saved.payload_json["reason"] == "my new supplement"


@pytest.mark.parametrize("tenant,property_id,allowed", [(4, 9, True), (4, 10, False), (5, 9, False)])
def test_dual_role_property_view_uses_actual_scope(access, tenant, property_id, allowed):
    from app.domain.identity import Principal, RoleGrant, OrganizationScope, ScopeType
    s, _ = access
    actor = Principal(7, "ACTIVE", "synthetic", (
        RoleGrant("PROPERTY_ADMIN", OrganizationScope(ScopeType.PROPERTY, tenant, property_id),
                  frozenset({WORK_ORDER_READ, "work_order:dispatch"})),
    ))
    s._reload_actor.return_value = actor
    if allowed:
        assert s.get_work_order_detail(actor=actor, work_order_id=41, audience="property")[0].id == 41
    else:
        with pytest.raises(AppError) as error:
            s.get_work_order_detail(actor=actor, work_order_id=41, audience="property")
        assert error.value.status_code == 403


def test_internal_timestamps_do_not_survive_in_display_metadata():
    service = MagicMock()
    raw = payload_context()
    raw[7][1].created_at = datetime(2026, 10, 31, 12, 34, tzinfo=UTC)
    service.get_work_order_detail.return_value = raw
    service.is_overdue.return_value = False
    data = get_work_order(41, Request({"type": "http", "headers": []}), MagicMock(), service)["data"]
    assert "2026-10-31T12:34:00Z" not in data["display"]["timestamp_labels"]
