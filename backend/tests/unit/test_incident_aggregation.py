"""Common-incident aggregation and shared-dispatch regression coverage."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import mysql

from app.core.config import Settings
from app.core.database import OWNED_TXN_KEY, OWNED_TXN_OBJ_KEY
from app.domain.ai import AnalysisStatus
from app.repository.ai import AiRepository
from app.repository.work_order import AutoAssignmentProvenance, WorkOrderRepository
from app.service.auto_dispatch import (
    _notification_intent_snapshot,
    _restore_notification_intents,
)
from app.service.analysis import AnalysisService
from app.service.incident import (
    IncidentCapacityService,
    IncidentService,
    _clear_pending_incident_redispatch,
    _schedule_incident_redispatch,
)
from app.service.notification import (
    _clear_pending_notification_deliveries,
    _schedule_notification_deliveries,
)
from app.service.work_order import WorkOrderService
from app.domain.work_order import AssignmentStatus


def _owned_session() -> MagicMock:
    session = MagicMock()
    transaction = MagicMock()
    transaction.is_active = True
    session.info = {
        OWNED_TXN_KEY: 1,
        OWNED_TXN_OBJ_KEY: transaction,
    }
    session.in_transaction.return_value = True
    session.get_transaction.return_value = transaction
    return session


def test_cluster_without_building_still_requires_matching_subcategory() -> None:
    session = MagicMock()
    matching = SimpleNamespace(id=1, house_id=11)
    other = SimpleNamespace(id=2, house_id=12)
    session.scalars.return_value.all.return_value = (matching, other)
    repository = AiRepository(session)
    repository.get_current_analysis = MagicMock(
        side_effect=(
            SimpleNamespace(status="COMPLETED", subcategory="outlet_spark"),
            SimpleNamespace(status="COMPLETED", subcategory="light_failure"),
        )
    )
    now = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)

    result = repository.list_cluster_work_orders(
        tenant_id=1,
        property_id=2,
        building_id=None,
        category="ELECTRICAL",
        subcategory="outlet_spark",
        since=now - timedelta(minutes=10),
        until=now + timedelta(minutes=10),
    )

    assert result == (matching,)
    repository.get_current_analysis.assert_any_call(matching.id)
    repository.get_current_analysis.assert_any_call(other.id)


def test_analysis_content_lookup_is_a_current_locking_read() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    repository = WorkOrderRepository(session)

    assert repository.get_content_for_update(41) is None

    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "work_order_contents.work_order_id = %s" in sql
    assert "FOR UPDATE" in sql


def test_analysis_apply_uses_locking_content_read_before_aggregation() -> None:
    session = MagicMock()
    session.info = {}
    session.in_transaction.return_value = False
    session.get_transaction.return_value = MagicMock()
    service = AnalysisService.__new__(AnalysisService)
    service._session = session
    service._work_orders = MagicMock()
    service._ai = MagicMock()
    service._append_failure_audit = MagicMock()
    service._validate_payload = MagicMock(return_value=(None, None))
    record = SimpleNamespace(id=41, status="CANCELLED")
    analysis = SimpleNamespace(
        id=51,
        status=AnalysisStatus.PROCESSING.value,
        error_code=None,
    )
    service._work_orders.get_work_order_for_update.return_value = record
    service._ai.get_current_analysis_for_update.return_value = analysis

    result = service._apply(
        snapshot=SimpleNamespace(work_order_id=41, analysis_id=51),
        bundle=SimpleNamespace(asr_text=None, llm_payload=None),
        request_id="analysis-current-read",
        attempt=1,
    )

    assert result is analysis
    service._work_orders.get_content_for_update.assert_called_once_with(record.id)
    service._work_orders.get_content.assert_not_called()


def test_shared_0007_without_assignment_extensions_fails_closed() -> None:
    session = MagicMock()
    session.get_bind.return_value = None
    repository = WorkOrderRepository(session)

    assert repository.get_auto_assignment_provenance(11) is None
    assert (
        repository.mark_auto_incident_capacity_owner(11, incident_id=501)
        is False
    )


def test_aggregate_locks_property_and_wakes_prior_pending_member_after_commit() -> None:
    session = _owned_session()
    settings = Settings(
        _env_file=None,
        app_env="test",
        incident_threshold=3,
        incident_window_minutes=10,
    )
    service = IncidentService(session, settings=settings, llm=MagicMock())
    service._ai = MagicMock()
    service._work_orders = MagicMock()
    service._audit = MagicMock()
    created_at = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    assigned = SimpleNamespace(
        id=101,
        tenant_id=1,
        property_id=2,
        category="ELECTRICAL",
        created_at=created_at,
        status="ASSIGNED",
        current_assignment_id=11,
        incident_id=None,
    )
    waiting = SimpleNamespace(
        id=102,
        tenant_id=1,
        property_id=2,
        category="ELECTRICAL",
        created_at=created_at + timedelta(minutes=1),
        status="PENDING_ASSIGNMENT",
        current_assignment_id=None,
        incident_id=None,
    )
    current = SimpleNamespace(
        id=103,
        tenant_id=1,
        property_id=2,
        house_id=4,
        category="ELECTRICAL",
        created_at=created_at + timedelta(minutes=2),
        status="PENDING_ASSIGNMENT",
        current_assignment_id=None,
        incident_id=None,
    )
    cluster = (assigned, waiting, current)
    service._work_orders.get_property_for_update.return_value = SimpleNamespace(
        id=2,
        tenant_id=1,
    )
    service._work_orders.get_active_assignment.side_effect = lambda order_id: (
        SimpleNamespace(id=11, employee_id=7) if order_id == assigned.id else None
    )
    service._ai.get_building_id.return_value = 9
    service._ai.list_cluster_work_orders.return_value = cluster
    service._ai.find_open_incident.return_value = None

    def add_incident(incident):
        incident.id = 501
        return incident

    service._ai.add_incident.side_effect = add_incident
    service._ai.get_incident_link.return_value = None
    service._ai.list_incident_work_orders.return_value = (
        SimpleNamespace(work_order_id=101),
        SimpleNamespace(work_order_id=102),
        SimpleNamespace(work_order_id=103),
    )

    incident = service.aggregate_locked(
        current,
        subcategory="outlet_spark",
        request_id="incident-aggregate-103",
    )

    assert incident is not None
    assert incident.subcategory == "outlet_spark"
    assert incident.primary_work_order_id == assigned.id
    assert incident.primary_employee_id == 7
    assert incident.work_order_count == 3
    assert all(item.incident_id == incident.id for item in cluster)
    service._work_orders.get_property_for_update.assert_called_once_with(2)
    cluster_call = service._ai.list_cluster_work_orders.call_args.kwargs
    assert cluster_call["subcategory"] == "outlet_spark"
    assert cluster_call["since"] == current.created_at - timedelta(minutes=10)
    assert cluster_call["until"] == current.created_at + timedelta(minutes=10)
    assert session.info["pending_incident_redispatch"] == {
        102: "incident:501:redispatch:102"
    }


def test_after_commit_enqueues_each_prior_pending_member_once(monkeypatch) -> None:
    session = MagicMock()
    session.in_nested_transaction.side_effect = (True, False, False)
    settings = Settings(_env_file=None, app_env="test")
    session.info = {
        "pending_incident_redispatch": {
            102: "incident:501:redispatch:102",
            101: "incident:501:redispatch:101",
        },
        "incident_redispatch_settings": settings,
    }
    enqueue = MagicMock(return_value=True)
    monkeypatch.setattr("app.worker.enqueue.enqueue_celery_task", enqueue)

    _schedule_incident_redispatch(session)
    assert enqueue.call_count == 0
    assert "pending_incident_redispatch" in session.info

    _schedule_incident_redispatch(session)
    _schedule_incident_redispatch(session)

    assert [call.kwargs["args"] for call in enqueue.call_args_list] == [
        (101, "incident:501:redispatch:101"),
        (102, "incident:501:redispatch:102"),
    ]
    assert "pending_incident_redispatch" not in session.info
    assert "incident_redispatch_settings" not in session.info


def test_nested_transaction_events_preserve_outer_delivery_intents(monkeypatch) -> None:
    session = MagicMock()
    settings = Settings(_env_file=None, app_env="test")
    session.info = {
        "pending_incident_redispatch": {
            102: "incident:501:redispatch:102",
        },
        "incident_redispatch_settings": settings,
        "pending_notification_deliveries": [71],
        "notification_settings": settings,
    }
    session.in_nested_transaction.return_value = True
    schedule_delivery = MagicMock(return_value=True)
    monkeypatch.setattr(
        "app.service.notification.schedule_notification_delivery",
        schedule_delivery,
    )

    _schedule_incident_redispatch(session)
    _schedule_notification_deliveries(session)
    _clear_pending_incident_redispatch(session)
    _clear_pending_notification_deliveries(session)

    assert session.info["pending_incident_redispatch"] == {
        102: "incident:501:redispatch:102"
    }
    assert session.info["pending_notification_deliveries"] == [71]
    schedule_delivery.assert_not_called()

    session.in_nested_transaction.return_value = False
    _schedule_notification_deliveries(session)

    schedule_delivery.assert_called_once_with(settings, 71)
    assert "pending_notification_deliveries" not in session.info


def test_nested_notification_rollback_restores_outer_intents_only() -> None:
    session = MagicMock()
    outer_settings = object()
    inner_settings = object()
    session.info = {
        "pending_notification_deliveries": [71],
        "notification_settings": outer_settings,
    }
    snapshot = _notification_intent_snapshot(session)
    session.info["pending_notification_deliveries"].append(72)
    session.info["notification_settings"] = inner_settings
    session.in_nested_transaction.return_value = True

    _clear_pending_notification_deliveries(session)
    _restore_notification_intents(session, snapshot)

    assert session.info["pending_notification_deliveries"] == [71]
    assert session.info["notification_settings"] is outer_settings


def test_primary_end_transfers_capacity_charge_to_active_follower() -> None:
    session = MagicMock()
    ai = MagicMock()
    work_orders = MagicMock()
    service = IncidentCapacityService(
        session,
        ai_repository=ai,
        work_order_repository=work_orders,
    )
    incident = SimpleNamespace(
        id=501,
        tenant_id=1,
        property_id=2,
        primary_work_order_id=101,
        primary_employee_id=7,
    )
    primary_order = SimpleNamespace(
        id=101,
        tenant_id=1,
        property_id=2,
        incident_id=501,
    )
    primary_assignment = SimpleNamespace(id=11, employee_id=7)
    follower_order = SimpleNamespace(id=102)
    follower_assignment = SimpleNamespace(id=12, employee_id=7)
    ai.get_incident_for_update.return_value = incident
    ai.list_active_incident_assignments_for_update.return_value = (
        (follower_order, follower_assignment),
    )
    work_orders.get_auto_assignment_provenance.side_effect = (
        AutoAssignmentProvenance(501, False, True),
        AutoAssignmentProvenance(501, True, False),
    )
    work_orders.mark_auto_incident_capacity_owner.return_value = True

    release = service.should_release_load(
        work_order=primary_order,
        assignment=primary_assignment,
    )

    assert release is False
    assert incident.primary_work_order_id == follower_order.id
    assert incident.primary_employee_id == follower_assignment.employee_id
    work_orders.mark_auto_incident_capacity_owner.assert_called_once_with(
        follower_assignment.id,
        incident_id=incident.id,
    )


def test_last_transferred_owner_releases_capacity_without_changing_other_primary() -> None:
    session = MagicMock()
    ai = MagicMock()
    work_orders = MagicMock()
    service = IncidentCapacityService(
        session,
        ai_repository=ai,
        work_order_repository=work_orders,
    )
    incident = SimpleNamespace(
        id=501,
        tenant_id=1,
        property_id=2,
        primary_work_order_id=201,
        primary_employee_id=8,
    )
    old_follower = SimpleNamespace(
        id=102,
        tenant_id=1,
        property_id=2,
        incident_id=501,
    )
    assignment = SimpleNamespace(id=12, employee_id=7)
    ai.get_incident_for_update.return_value = incident
    ai.list_active_incident_assignments_for_update.return_value = ()
    work_orders.get_auto_assignment_provenance.return_value = (
        AutoAssignmentProvenance(501, True, True)
    )

    release = service.should_release_load(
        work_order=old_follower,
        assignment=assignment,
    )

    assert release is True
    assert incident.primary_work_order_id == 201
    assert incident.primary_employee_id == 8


def test_primary_end_without_follower_promotes_live_incident_assignment() -> None:
    session = MagicMock()
    ai = MagicMock()
    work_orders = MagicMock()
    service = IncidentCapacityService(
        session,
        ai_repository=ai,
        work_order_repository=work_orders,
    )
    incident = SimpleNamespace(
        id=501,
        tenant_id=1,
        property_id=2,
        primary_work_order_id=101,
        primary_employee_id=7,
    )
    primary_order = SimpleNamespace(
        id=101,
        tenant_id=1,
        property_id=2,
        incident_id=501,
    )
    primary_assignment = SimpleNamespace(id=11, employee_id=7)
    successor_order = SimpleNamespace(id=103)
    successor_assignment = SimpleNamespace(id=13, employee_id=8)
    ai.get_incident_for_update.return_value = incident
    ai.list_active_incident_assignments_for_update.return_value = (
        (successor_order, successor_assignment),
    )
    work_orders.get_auto_assignment_provenance.side_effect = (
        AutoAssignmentProvenance(501, False, True),
        None,
    )

    release = service.should_release_load(
        work_order=primary_order,
        assignment=primary_assignment,
    )

    assert release is True
    assert incident.primary_work_order_id == successor_order.id
    assert incident.primary_employee_id == successor_assignment.employee_id


def test_new_assignment_adopts_empty_incident_primary() -> None:
    session = MagicMock()
    ai = MagicMock()
    work_orders = MagicMock()
    service = IncidentCapacityService(
        session,
        ai_repository=ai,
        work_order_repository=work_orders,
    )
    incident = SimpleNamespace(
        id=501,
        tenant_id=1,
        property_id=2,
        primary_work_order_id=None,
        primary_employee_id=None,
    )
    record = SimpleNamespace(
        id=101,
        tenant_id=1,
        property_id=2,
        incident_id=501,
    )
    assignment = SimpleNamespace(id=11, employee_id=7)
    ai.get_incident_for_update.return_value = incident

    service.adopt_assignment(work_order=record, assignment=assignment)

    assert incident.primary_work_order_id == record.id
    assert incident.primary_employee_id == assignment.employee_id


def test_ending_incident_follower_does_not_release_shared_load(monkeypatch) -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._session = MagicMock()
    service._repository = MagicMock()
    service._employees = MagicMock()
    service._adjust_load = MagicMock()
    record = SimpleNamespace(id=103, current_assignment_id=91)
    assignment = SimpleNamespace(
        id=91,
        employee_id=7,
        status="ACTIVE",
        ended_at=None,
        reason=None,
    )
    service._repository.get_active_assignment_for_update.return_value = assignment
    service._employees.get_employee.return_value = SimpleNamespace(id=7)
    capacity = MagicMock()
    capacity.should_release_load.return_value = False
    monkeypatch.setattr(
        "app.service.incident.IncidentCapacityService",
        MagicMock(return_value=capacity),
    )
    now = datetime(2026, 9, 20, 9, 10, tzinfo=UTC)

    service._end_active_assignment(
        record,
        now=now,
        status=AssignmentStatus.REASSIGNED,
        reason="SUPERVISOR_REASSIGN",
    )

    assert assignment.status == "REASSIGNED"
    assert assignment.ended_at == now
    assert record.current_assignment_id is None
    service._adjust_load.assert_not_called()
    capacity.should_release_load.assert_called_once_with(
        work_order=record,
        assignment=assignment,
    )
