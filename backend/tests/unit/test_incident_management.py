"""Incident management API policy and Mini Program wiring tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql

from app.core.errors import AiErrorCode, AppError, IamErrorCode, IncidentErrorCode
from app.domain.ai import INCIDENT_MANAGE, INCIDENT_READ
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.provider.errors import ProviderTimeoutError
from app.repository.ai import AiRepository
from app.service.incident import IncidentService


NOW = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _grant(
    role_code: str,
    *permissions: str,
    property_id: int = 10,
    scope_type: ScopeType = ScopeType.PROPERTY,
) -> RoleGrant:
    return RoleGrant(
        role_code=role_code,
        scope=OrganizationScope(
            scope_type,
            tenant_id=1,
            property_id=property_id if scope_type is ScopeType.PROPERTY else None,
        ),
        permissions=frozenset(permissions),
    )


def _actor(*grants: RoleGrant) -> Principal:
    return Principal(
        user_id=7,
        status="ACTIVE",
        display_name="Synthetic actor",
        grants=tuple(grants),
    )


def _incident(*, property_id: int = 10, updated_at: datetime = NOW):
    return SimpleNamespace(
        id=501,
        tenant_id=1,
        property_id=property_id,
        building_id=21,
        status="OPEN",
        category="ELECTRICAL",
        subcategory="outlet_spark",
        summary=None,
        ai_summary=None,
        affected_scope=None,
        primary_work_order_id=101,
        primary_employee_id=31,
        work_order_count=3,
        first_seen_at=NOW - timedelta(minutes=2),
        last_seen_at=NOW,
        updated_at=updated_at,
    )


def _service(*, principal_loader=None) -> IncidentService:
    service = IncidentService.__new__(IncidentService)
    service._session = MagicMock()
    service._session.info = {}
    service._session.in_transaction.return_value = False
    service._session.begin.return_value = MagicMock()
    service._ai = MagicMock()
    service._employees = MagicMock()
    service._employees.find_effective_assignment.return_value = object()
    service._work_orders = MagicMock()
    service._audit = MagicMock()
    service._llm = MagicMock()
    service._principal_loader = principal_loader or (lambda _user_id: _manager())
    return service


def _manager(*, property_id: int = 10) -> Principal:
    return _actor(
        _grant(
            "SUPERVISOR",
            INCIDENT_READ,
            INCIDENT_MANAGE,
            property_id=property_id,
        )
    )


def _staff(*, property_id: int = 10) -> Principal:
    return _actor(_grant("STAFF", INCIDENT_READ, property_id=property_id))


def test_staff_list_uses_current_participant_query_before_pagination() -> None:
    service = _service()
    actor = _staff()
    record = _incident()
    service._work_orders.get_property.return_value = SimpleNamespace(id=10, tenant_id=1)
    service._employees.get_employee_for_user.return_value = SimpleNamespace(
        id=31,
        tenant_id=1,
        status="ACTIVE",
    )
    service._ai.list_incidents_for_participant.return_value = (record,)

    result = service.list_incidents(
        actor=actor,
        property_id=10,
        tenant_id=1,
        status="OPEN",
        after_id=400,
        limit=25,
    )

    assert result == (record,)
    service._ai.list_incidents_for_participant.assert_called_once_with(
        tenant_id=1,
        property_id=10,
        employee_id=31,
        status="OPEN",
        after_id=400,
        limit=25,
    )
    service._ai.list_incidents.assert_not_called()


def test_non_employee_reader_gets_an_empty_list() -> None:
    service = _service()
    service._work_orders.get_property.return_value = SimpleNamespace(id=10, tenant_id=1)
    service._employees.get_employee_for_user.return_value = None

    assert service.list_incidents(actor=_staff(), property_id=10) == ()
    service._ai.list_incidents_for_participant.assert_not_called()
    service._ai.list_incidents.assert_not_called()


def test_manager_list_keeps_full_scope_query() -> None:
    service = _service()
    record = _incident()
    service._work_orders.get_property.return_value = SimpleNamespace(id=10, tenant_id=1)
    service._ai.list_incidents.return_value = (record,)

    result = service.list_incidents(
        actor=_manager(),
        property_id=10,
        tenant_id=1,
        status="MONITORING",
        after_id=0,
        limit=50,
    )

    assert result == (record,)
    service._ai.list_incidents.assert_called_once_with(
        tenant_id=1,
        property_id=10,
        status="MONITORING",
        after_id=0,
        limit=50,
    )
    service._ai.list_incidents_for_participant.assert_not_called()


def test_mixed_role_uses_manage_permission_for_the_target_property_only() -> None:
    actor = _actor(
        _grant("STAFF", INCIDENT_READ, property_id=10),
        _grant(
            "SUPERVISOR",
            INCIDENT_READ,
            INCIDENT_MANAGE,
            property_id=11,
        ),
    )
    service = _service()
    service._work_orders.get_property.side_effect = (
        SimpleNamespace(id=10, tenant_id=1),
        SimpleNamespace(id=11, tenant_id=1),
    )
    service._employees.get_employee_for_user.return_value = SimpleNamespace(
        id=31,
        tenant_id=1,
        status="ACTIVE",
    )
    service._ai.list_incidents_for_participant.return_value = ()
    service._ai.list_incidents.return_value = ()

    service.list_incidents(actor=actor, property_id=10)
    service.list_incidents(actor=actor, property_id=11)

    service._ai.list_incidents_for_participant.assert_called_once()
    service._ai.list_incidents.assert_called_once()
    assert service._ai.list_incidents.call_args.kwargs["property_id"] == 11


def test_staff_detail_rejects_same_property_non_participant() -> None:
    service = _service()
    service._ai.get_incident.return_value = _incident()
    service._employees.get_employee_for_user.return_value = SimpleNamespace(
        id=31,
        tenant_id=1,
        status="ACTIVE",
    )
    service._ai.is_incident_participant.return_value = False

    with pytest.raises(AppError) as captured:
        service.get_incident(actor=_staff(), incident_id=501)

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert captured.value.status_code == 403


def test_staff_detail_allows_current_participant_but_not_summary() -> None:
    service = _service(principal_loader=lambda _user_id: _staff())
    incident = _incident()
    service._ai.get_incident.return_value = incident
    service._employees.get_employee_for_user.return_value = SimpleNamespace(
        id=31,
        tenant_id=1,
        status="ACTIVE",
    )
    service._ai.is_incident_participant.return_value = True

    assert service.get_incident(actor=_staff(), incident_id=501) is incident

    service._ai.get_incident_for_update.return_value = incident
    with pytest.raises(AppError) as captured:
        service.summarize(
            actor=_staff(),
            incident_id=501,
            request_id="incident-staff-summary",
        )
    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._llm.structured_completion.assert_not_called()


def test_manager_resolves_and_reopens_without_mutating_member_work_orders() -> None:
    manager = _manager()
    service = _service(principal_loader=lambda _user_id: manager)
    incident = _incident()
    service._ai.get_incident_for_update.return_value = incident

    resolved = service.update_status(
        actor=manager,
        incident_id=incident.id,
        target_status="RESOLVED",
        reason="现场公共线路已恢复，继续保留各工单回执",
        expected_updated_at=NOW,
        request_id="incident-resolve-501",
    )

    assert resolved.status == "RESOLVED"
    service._audit.append.assert_called_once()
    details = service._audit.append.call_args.kwargs["details"]
    assert details["from_status"] == "OPEN"
    assert details["to_status"] == "RESOLVED"
    assert details["reason"] == "现场公共线路已恢复，继续保留各工单回执"
    assert not any("work_order" in str(call).lower() for call in service._work_orders.mock_calls)

    service._audit.reset_mock()
    reopened = service.update_status(
        actor=manager,
        incident_id=incident.id,
        target_status="OPEN",
        reason="住户反馈公共故障再次出现",
        expected_updated_at=NOW,
        request_id="incident-reopen-501",
    )
    assert reopened.status == "OPEN"
    assert service._audit.append.call_args.kwargs["details"]["from_status"] == "RESOLVED"


def test_manager_can_traverse_every_frozen_disposition_transition() -> None:
    manager = _manager()
    service = _service(principal_loader=lambda _user_id: manager)
    incident = _incident()
    service._ai.get_incident_for_update.return_value = incident
    transitions = (
        ("MONITORING", "开始观察公共故障是否继续出现"),
        ("OPEN", "监控期间再次收到有效住户反馈"),
        ("RESOLVED", "公共故障已经处理并完成现场复核"),
        ("CLOSED", "观察期结束且没有新的关联故障"),
        ("OPEN", "关闭后出现新的有效故障证据"),
    )

    for target, reason in transitions:
        previous = incident.status
        service._audit.reset_mock()
        result = service.update_status(
            actor=manager,
            incident_id=incident.id,
            target_status=target,
            reason=reason,
            expected_updated_at=NOW,
            request_id=f"incident-transition-{previous.lower()}-{target.lower()}",
        )

        assert result.status == target
        details = service._audit.append.call_args.kwargs["details"]
        assert details["from_status"] == previous
        assert details["to_status"] == target
        assert details["reason"] == reason

    assert not any("work_order" in str(call).lower() for call in service._work_orders.mock_calls)


def test_incident_status_update_rejects_staff_stale_and_illegal_transition() -> None:
    staff = _staff()
    service = _service(principal_loader=lambda _user_id: staff)
    incident = _incident()
    service._ai.get_incident_for_update.return_value = incident
    with pytest.raises(AppError) as forbidden:
        service.update_status(
            actor=staff,
            incident_id=incident.id,
            target_status="RESOLVED",
            reason="无权限尝试",
            expected_updated_at=NOW,
            request_id="incident-forbidden",
        )
    assert forbidden.value.code == IamErrorCode.FORBIDDEN
    assert incident.status == "OPEN"

    manager = _manager()
    service = _service(principal_loader=lambda _user_id: manager)
    incident = _incident()
    service._ai.get_incident_for_update.return_value = incident
    with pytest.raises(AppError) as stale:
        service.update_status(
            actor=manager,
            incident_id=incident.id,
            target_status="RESOLVED",
            reason="基于旧页面提交",
            expected_updated_at=NOW - timedelta(seconds=1),
            request_id="incident-stale",
        )
    assert stale.value.code == IncidentErrorCode.CONFLICT
    assert incident.status == "OPEN"

    with pytest.raises(AppError) as illegal:
        service.update_status(
            actor=manager,
            incident_id=incident.id,
            target_status="OPEN",
            reason="重复状态",
            expected_updated_at=NOW,
            request_id="incident-noop",
        )
    assert illegal.value.code == IncidentErrorCode.CONFLICT
    service._audit.append.assert_not_called()


def test_participant_repository_query_filters_current_assignment_before_limit() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = ()
    repository = AiRepository(session)

    assert repository.list_incidents_for_participant(
        tenant_id=1,
        property_id=10,
        employee_id=31,
        status="OPEN",
        after_id=100,
        limit=25,
    ) == ()

    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "SELECT DISTINCT incidents" in sql
    assert "work_order_assignments.id = work_orders.current_assignment_id" in sql
    assert "work_order_assignments.status = %s" in sql
    assert "work_order_assignments.ended_at IS NULL" in sql
    assert "incidents.status = %s" in sql
    assert "LIMIT %s" in sql


def test_summary_provider_failure_is_not_reported_as_success() -> None:
    manager = _manager()
    service = _service(principal_loader=lambda _user_id: manager)
    incident = _incident()
    service._ai.get_incident_for_update.return_value = incident
    service._llm.structured_completion.side_effect = ProviderTimeoutError(
        "synthetic timeout"
    )

    with pytest.raises(AppError) as captured:
        service.summarize(
            actor=manager,
            incident_id=501,
            request_id="incident-provider-failure",
        )

    assert captured.value.code == AiErrorCode.TIMEOUT
    assert captured.value.status_code == 503
    assert incident.ai_summary is None
    service._audit.append.assert_not_called()


def test_summary_rechecks_permission_after_provider_call() -> None:
    manager = _manager()
    revoked = _actor(_grant("STAFF", INCIDENT_READ, property_id=10))
    loader = MagicMock(side_effect=(manager, revoked))
    service = _service(principal_loader=loader)
    incident = _incident()
    service._ai.get_incident_for_update.side_effect = (incident, incident)
    service._llm.structured_completion.return_value = {
        "summary": "Synthetic summary",
        "affected_scope": "Building 1",
        "common_symptom": "outlet sparking",
        "suggested_handling_note": "Inspect the shared circuit",
    }

    with pytest.raises(AppError) as captured:
        service.summarize(
            actor=manager,
            incident_id=501,
            request_id="incident-revoked-during-provider",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert incident.ai_summary is None
    service._audit.append.assert_not_called()


def test_summary_rejects_stale_result_after_incident_changes() -> None:
    manager = _manager()
    service = _service(principal_loader=lambda _user_id: manager)
    original = _incident()
    changed = _incident(updated_at=NOW + timedelta(seconds=1))
    changed.work_order_count = 4
    service._ai.get_incident_for_update.side_effect = (original, changed)
    service._llm.structured_completion.return_value = {
        "summary": "Synthetic summary",
        "affected_scope": "Building 1",
        "common_symptom": "outlet sparking",
        "suggested_handling_note": "Inspect the shared circuit",
    }

    with pytest.raises(AppError) as captured:
        service.summarize(
            actor=manager,
            incident_id=501,
            request_id="incident-stale-summary",
        )

    assert captured.value.code == IncidentErrorCode.CONFLICT
    assert captured.value.status_code == 409
    assert captured.value.details == {"retryable": True}
    assert changed.ai_summary is None
    service._audit.append.assert_not_called()


def test_incident_page_wires_scoped_list_detail_summary_and_draft() -> None:
    source = (
        REPO_ROOT / "miniprogram/pages/supervisor/incidents.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/supervisor/incidents.wxml"
    ).read_text(encoding="utf-8")
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    home = (REPO_ROOT / "miniprogram/pages/home/home.wxml").read_text(
        encoding="utf-8"
    )

    assert 'scopeAllows(scope, "incident:read", property)' in source
    assert 'scopeAllows(scope, "incident:manage", property)' in source
    assert "api.incident(id)" in source
    assert "api.summarizeIncident(incidentId)" in source
    assert "updateIncidentStatus(selected.id" in source
    assert "expected_updated_at: selected.updated_at" in source
    assert "createAnnouncement" in source
    assert "next_cursor" in source
    assert "小区 ID" not in template
    assert "生成 / 刷新 AI 运营摘要" in template
    assert "待人工审核的公告草稿" in template
    assert "不会自动改变事件状态或自动发布" in template
    assert "处置或重开原因（必填）" in template
    assert "不会批量关闭住户工单或覆盖回执" in template
    assert "/supervisor/incidents/${id}" in api
    assert "/supervisor/incidents/${id}/status" in api
    assert "&tenant_id=${tenantId}" in api
    assert "事件管理" in home
    assert "我参与的事件" in home


def test_staff_task_exposes_only_its_linked_incident_navigation() -> None:
    schema = (REPO_ROOT / "backend/app/schema/work_order.py").read_text(
        encoding="utf-8"
    )
    source = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/staff-orders/detail.wxml"
    ).read_text(encoding="utf-8")

    assert "incident_id: int | None" in schema
    assert "workOrder.incident_id" in source
    assert "查看本人参与的集中事件" in template
