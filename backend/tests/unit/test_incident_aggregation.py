"""Common-incident aggregation and shared-dispatch regression coverage."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import Settings
from app.core.database import OWNED_TXN_KEY, OWNED_TXN_OBJ_KEY
from app.repository.ai import AiRepository
from app.service.incident import (
    IncidentService,
    _clear_pending_incident_redispatch,
    _schedule_incident_redispatch,
)
from app.service.notification import (
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
        SimpleNamespace(employee_id=7) if order_id == assigned.id else None
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

    assert session.info["pending_incident_redispatch"] == {
        102: "incident:501:redispatch:102"
    }
    assert session.info["pending_notification_deliveries"] == [71]
    schedule_delivery.assert_not_called()

    session.in_nested_transaction.return_value = False
    _schedule_notification_deliveries(session)

    schedule_delivery.assert_called_once_with(settings, 71)
    assert "pending_notification_deliveries" not in session.info


def test_ending_incident_follower_does_not_release_shared_load() -> None:
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
    service._repository.is_auto_incident_follow_assignment.return_value = True
    service._employees.get_employee.return_value = SimpleNamespace(id=7)
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
