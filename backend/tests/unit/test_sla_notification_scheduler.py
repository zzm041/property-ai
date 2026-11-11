"""SLA state idempotency and bounded notification-delivery retries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import mysql

from app.core.config import Settings
from app.core.errors import NotificationErrorCode
from app.provider.errors import ProviderUnavailableError
from app.repository.work_order import WorkOrderRepository
from app.service.notification import (
    DELIVERY_MAX_ATTEMPTS,
    NotificationService,
    delivery_retry_delay_seconds,
)
from app.service.work_order.sla import (
    SlaService,
    reset_assignment_sla_deadlines,
    reset_response_sla_deadline,
)


def _session() -> MagicMock:
    session = MagicMock()
    session.info = {}
    session.in_transaction.return_value = False
    transaction = MagicMock()
    session.begin.return_value.__enter__.return_value = transaction
    session.get_transaction.return_value = transaction
    return session


class _SlaRepository:
    def __init__(self, clock, work_order) -> None:
        self.clock = clock
        self.work_order = work_order
        self.logs: list[object] = []

    def list_due_sla_clocks_for_update(self, *, now, limit, work_order_id=None):
        del limit
        if work_order_id is not None and work_order_id != self.clock.work_order_id:
            return ()
        if self.clock.state == "OPEN" and self.clock.warning_at <= now:
            return (self.clock,)
        if self.clock.state == "WARNING" and self.clock.complete_due_at <= now:
            return (self.clock,)
        return ()

    def get_work_order(self, work_order_id):
        assert work_order_id == self.work_order.id
        return self.work_order

    def add_log(self, record):
        self.logs.append(record)
        return record

    def list_sla_management_user_ids(self, *, tenant_id, property_id):
        assert (tenant_id, property_id) == (7, 9)
        return (20, 21)

    def get_active_assignee_user_id(self, work_order_id):
        assert work_order_id == self.work_order.id
        return 21


class _Notifier:
    calls: list[dict[str, object]] = []

    def __init__(self, session, settings=None) -> None:
        del session, settings

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=len(self.calls))


def test_sla_warning_then_overdue_is_idempotent(monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    clock = SimpleNamespace(
        id=31,
        work_order_id=41,
        warning_at=now,
        complete_due_at=now + timedelta(minutes=10),
        state="OPEN",
    )
    work_order = SimpleNamespace(
        id=41,
        tenant_id=7,
        property_id=9,
        status="PROCESSING",
        process_reason="NONE",
    )
    repository = _SlaRepository(clock, work_order)
    _Notifier.calls = []
    monkeypatch.setattr("app.service.work_order.sla.NotificationService", _Notifier)
    service = SlaService(_session(), repository=repository)

    warning = service.sweep_due(now=now)
    duplicate_warning = service.sweep_due(now=now)
    overdue = service.sweep_due(now=now + timedelta(minutes=10))
    duplicate_overdue = service.sweep_due(now=now + timedelta(minutes=10))

    assert warning.as_dict() == {
        "scanned": 1,
        "warnings": 1,
        "overdue": 0,
        "stopped": 0,
        "notifications": 2,
    }
    assert duplicate_warning.scanned == 0
    assert overdue.overdue == 1
    assert overdue.notifications == 2
    assert duplicate_overdue.scanned == 0
    assert clock.state == "OVERDUE"
    assert [item.event_code for item in repository.logs] == [
        "SLA_WARNING",
        "SLA_OVERDUE",
    ]
    assert [item.request_id for item in repository.logs] == [
        "sla-clock:31:warning",
        "sla-clock:31:overdue",
    ]
    assert len(_Notifier.calls) == 4
    assert {item["user_id"] for item in _Notifier.calls} == {20, 21}


def test_sla_sweep_stops_terminal_order_without_notification(monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    clock = SimpleNamespace(
        id=31,
        work_order_id=41,
        warning_at=now,
        complete_due_at=now,
        state="OPEN",
    )
    work_order = SimpleNamespace(
        id=41,
        tenant_id=7,
        property_id=9,
        status="CLOSED",
        process_reason="NONE",
    )
    repository = _SlaRepository(clock, work_order)
    _Notifier.calls = []
    monkeypatch.setattr("app.service.work_order.sla.NotificationService", _Notifier)

    result = SlaService(_session(), repository=repository).sweep_due(now=now)

    assert result.stopped == 1
    assert result.notifications == 0
    assert clock.state == "STOPPED"
    assert repository.logs == []


def test_assignment_accept_timeout_releases_load_and_enqueues_once(monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    clock = SimpleNamespace(
        id=51,
        work_order_id=61,
        accept_due_at=now,
        respond_due_at=now + timedelta(minutes=10),
    )
    record = SimpleNamespace(
        id=61,
        tenant_id=7,
        property_id=9,
        category="PLUMBING",
        priority="MEDIUM",
        status="ASSIGNED",
        process_reason="NONE",
        current_assignment_id=71,
        version=3,
    )
    assignment = SimpleNamespace(
        id=71,
        employee_id=81,
        status="ACTIVE",
        timeout_at=None,
        ended_at=None,
        reason=None,
    )
    repository = MagicMock()
    repository.list_due_assignment_sla_work_orders_for_update.side_effect = [
        (record,),
        (),
    ]
    repository.get_sla_clock_for_update.return_value = clock
    repository.get_active_assignment_for_update.return_value = assignment
    repository.get_active_assignee_user_id.return_value = 22
    repository.get_property.return_value = SimpleNamespace(timezone="Asia/Shanghai")
    repository.list_sla_management_user_ids.return_value = (20, 21)
    employee_repository = MagicMock()
    employee_repository.get_employee.return_value = SimpleNamespace(id=81)
    daily = SimpleNamespace(current_load=2)
    employee_repository.get_daily_status_for_update.return_value = daily
    audit = MagicMock()
    _Notifier.calls = []
    monkeypatch.setattr("app.service.work_order.sla.NotificationService", _Notifier)
    service = SlaService(
        _session(),
        repository=repository,
        settings=Settings(app_env="test"),
    )
    service._employees = employee_repository
    service._audit = audit
    service._enqueue_redispatch = MagicMock(return_value=True)

    first = service.sweep_assignment_deadlines(now=now)
    duplicate = service.sweep_assignment_deadlines(now=now)

    assert first.as_dict() == {
        "scanned": 1,
        "accept_timeouts": 1,
        "response_timeouts": 0,
        "notifications": 3,
        "redispatch_enqueued": 1,
    }
    assert duplicate.scanned == 0
    assert record.status == "PENDING_ASSIGNMENT"
    assert record.current_assignment_id is None
    assert record.version == 4
    assert assignment.status == "REASSIGNED"
    assert assignment.timeout_at == now
    assert assignment.ended_at == now
    assert assignment.reason == "SLA_ACCEPT_TIMEOUT"
    assert daily.current_load == 1
    log = repository.add_log.call_args.args[0]
    assert log.event_code == "REASSIGN"
    assert log.request_id == "sla-assignment:71:accept-timeout"
    service._enqueue_redispatch.assert_called_once_with(
        61,
        "sla-assignment:71:accept-timeout",
    )
    assert {item["user_id"] for item in _Notifier.calls} == {20, 21, 22}
    audit.append.assert_called_once()


def test_response_timeout_escalates_once_without_reassignment(monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    clock = SimpleNamespace(
        id=52,
        work_order_id=62,
        accept_due_at=now - timedelta(minutes=10),
        respond_due_at=now,
    )
    record = SimpleNamespace(
        id=62,
        tenant_id=7,
        property_id=9,
        category="PLUMBING",
        priority="MEDIUM",
        status="ACCEPTED",
        process_reason="NONE",
    )
    repository = MagicMock()
    repository.list_due_assignment_sla_work_orders_for_update.return_value = (record,)
    repository.get_sla_clock_for_update.return_value = clock
    assignment = SimpleNamespace(
        id=72,
        employee_id=82,
        accept_at=now - timedelta(minutes=10),
        timeout_at=None,
    )
    repository.get_active_assignment_for_update.return_value = assignment
    record.current_assignment_id = 72
    repository.has_log_request_id.side_effect = [False, True]
    repository.list_sla_management_user_ids.return_value = (20, 21)
    repository.get_active_assignee_user_id.return_value = 21
    _Notifier.calls = []
    monkeypatch.setattr("app.service.work_order.sla.NotificationService", _Notifier)
    service = SlaService(_session(), repository=repository)

    first = service.sweep_assignment_deadlines(now=now)
    duplicate = service.sweep_assignment_deadlines(now=now)

    assert first.response_timeouts == 1
    assert first.notifications == 2
    assert first.redispatch_enqueued == 0
    assert duplicate.response_timeouts == 0
    assert duplicate.notifications == 0
    assert record.status == "ACCEPTED"
    log = repository.add_log.call_args.args[0]
    assert log.event_code == "SLA_RESPONSE_TIMEOUT"
    assert log.request_id == "sla-assignment:72:response-timeout"
    assert assignment.timeout_at == now
    assert len(_Notifier.calls) == 2


def test_due_assignment_query_filters_handled_cycles_before_limit() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = WorkOrderRepository(session)

    assert repository.list_due_assignment_sla_work_orders_for_update(
        now=datetime(2026, 9, 20, 8, 0, tzinfo=UTC),
        limit=100,
    ) == ()

    statement = session.scalars.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "work_order_assignments.id = work_orders.current_assignment_id" in sql
    assert "work_order_assignments.status = 'ACTIVE'" in sql
    assert "work_order_assignments.timeout_at IS NULL" in sql
    assert "work_order_assignments.ended_at IS NULL" in sql
    assert "work_order_assignments.accept_at IS NULL" in sql
    assert "work_order_assignments.accept_at IS NOT NULL" in sql
    assert "NOT (EXISTS" in sql
    assert "work_order_logs.work_order_id = work_orders.id" in sql
    assert "concat('sla-assignment:', work_order_assignments.id" in sql
    assert "LIMIT 100 FOR UPDATE SKIP LOCKED" in sql


def test_versioned_due_query_waits_until_response_escalation_due() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = WorkOrderRepository(session)
    repository.sla_versioning_available = MagicMock(return_value=True)

    assert repository.list_due_assignment_sla_work_orders_for_update(
        now=datetime(2026, 9, 20, 8, 0, tzinfo=UTC),
        limit=100,
    ) == ()

    statement = session.scalars.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN work_order_sla_applications" in sql
    assert "timestampadd(MINUTE, coalesce(work_order_sla_applications.escalation_minutes, 0), sla_clocks.respond_due_at)" in sql


def test_assignment_and_accept_restart_phase_deadlines() -> None:
    assigned_at = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    accepted_at = assigned_at + timedelta(minutes=2)
    clock = SimpleNamespace(accept_due_at=None, respond_due_at=None)
    record = SimpleNamespace(
        id=63,
        tenant_id=7,
        property_id=9,
        category="PLUMBING",
        priority="MEDIUM",
    )
    repository = MagicMock()
    repository.get_sla_clock_for_update.return_value = clock
    repository.find_sla_policy.return_value = SimpleNamespace(respond_minutes=5)

    reset_assignment_sla_deadlines(
        repository,
        work_order=record,
        now=assigned_at,
    )
    assert clock.accept_due_at == assigned_at + timedelta(minutes=5)
    assert clock.respond_due_at == assigned_at + timedelta(minutes=5)

    reset_response_sla_deadline(
        repository,
        work_order=record,
        now=accepted_at,
    )
    assert clock.respond_due_at == accepted_at + timedelta(minutes=5)


def test_versioned_assignment_uses_snapshot_accept_respond_and_escalation() -> None:
    assigned_at = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    accepted_at = assigned_at + timedelta(minutes=3)
    clock = SimpleNamespace(accept_due_at=None, respond_due_at=None)
    record = SimpleNamespace(
        id=64,
        tenant_id=7,
        property_id=9,
        category="PLUMBING",
        priority="MEDIUM",
    )
    application = SimpleNamespace(
        id=91,
        policy_version_id=81,
        accept_minutes=4,
        respond_minutes=11,
        escalation_minutes=7,
    )
    repository = MagicMock()
    repository.sla_versioning_available.return_value = True
    repository.get_sla_application.return_value = application
    repository.get_sla_clock_for_update.return_value = clock

    reset_assignment_sla_deadlines(repository, work_order=record, now=assigned_at)
    assert clock.accept_due_at == assigned_at + timedelta(minutes=4)
    assert clock.respond_due_at == assigned_at + timedelta(minutes=11)

    reset_response_sla_deadline(repository, work_order=record, now=accepted_at)
    assert clock.respond_due_at == accepted_at + timedelta(minutes=11)


def test_response_escalation_waits_for_versioned_grace(monkeypatch) -> None:
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    clock = SimpleNamespace(id=53, work_order_id=63, respond_due_at=now)
    record = SimpleNamespace(
        id=63,
        tenant_id=7,
        property_id=9,
        category="PLUMBING",
        priority="MEDIUM",
        status="ACCEPTED",
        process_reason="NONE",
        current_assignment_id=73,
    )
    assignment = SimpleNamespace(id=73, employee_id=83, accept_at=now - timedelta(minutes=10), timeout_at=None)
    repository = MagicMock()
    repository.get_active_assignment_for_update.return_value = assignment
    repository.sla_versioning_available.return_value = True
    repository.get_sla_application.return_value = SimpleNamespace(
        id=92,
        policy_version_id=82,
        accept_minutes=4,
        respond_minutes=10,
        escalation_minutes=7,
    )
    repository.has_log_request_id.return_value = False
    repository.list_sla_management_user_ids.return_value = (20,)
    repository.get_active_assignee_user_id.return_value = None
    _Notifier.calls = []
    monkeypatch.setattr("app.service.work_order.sla.NotificationService", _Notifier)
    service = SlaService(_session(), repository=repository)

    assert service._escalate_response_timeout(
        record=record,
        clock=clock,
        now=now + timedelta(minutes=6),
        notifier=_Notifier(None),
    ) is None
    assert assignment.timeout_at is None

    count = service._escalate_response_timeout(
        record=record,
        clock=clock,
        now=now + timedelta(minutes=7),
        notifier=_Notifier(None),
    )
    assert count == 1
    log = repository.add_log.call_args.args[0]
    assert log.payload_json["escalation_due_at"] == (now + timedelta(minutes=7)).isoformat()
    assert log.payload_json["policy_version_id"] == 82


class _DeliveryRepository:
    def __init__(self) -> None:
        self.delivery = SimpleNamespace(
            id=5,
            notification_id=6,
            status="PENDING",
            attempt=0,
            next_retry_at=None,
            last_error_code=None,
            delivered_at=None,
        )
        self.notification = SimpleNamespace(
            id=6,
            user_id=7,
            title="title",
            body="body",
        )

    def claim_pending_delivery(self, delivery_id, *, now, lease_until, claim_code):
        if delivery_id != self.delivery.id or self.delivery.status != "PENDING":
            return None
        if self.delivery.next_retry_at is not None and self.delivery.next_retry_at > now:
            return None
        self.delivery.attempt += 1
        self.delivery.next_retry_at = lease_until
        self.delivery.last_error_code = claim_code
        return self.delivery

    def get_notification(self, notification_id):
        return self.notification if notification_id == self.notification.id else None

    def finish_claimed_delivery(
        self,
        *,
        delivery_id,
        attempt,
        claim_code,
        status,
        last_error_code,
        delivered_at,
        next_retry_at,
        update_lease,
    ):
        if (
            delivery_id != self.delivery.id
            or attempt != self.delivery.attempt
            or claim_code != self.delivery.last_error_code
            or self.delivery.status != "PENDING"
        ):
            return False
        if status is not None:
            self.delivery.status = status
        self.delivery.last_error_code = last_error_code
        if delivered_at is not None or status == "SUCCEEDED":
            self.delivery.delivered_at = delivered_at
        if update_lease:
            self.delivery.next_retry_at = next_retry_at
        return True

    def get_delivery(self, delivery_id):
        return self.delivery if delivery_id == self.delivery.id else None


class _UnavailableProvider:
    def __init__(self) -> None:
        self.calls = 0

    def deliver_push(self, **kwargs) -> None:
        del kwargs
        self.calls += 1
        raise ProviderUnavailableError("unavailable")


def test_notification_provider_failure_retries_then_becomes_terminal(monkeypatch) -> None:
    repository = _DeliveryRepository()
    provider = _UnavailableProvider()
    monkeypatch.setattr(
        "app.service.notification.build_notification_provider",
        lambda settings: provider,
    )
    service = NotificationService(_session(), repository=repository)

    outcomes = []
    for _ in range(DELIVERY_MAX_ATTEMPTS):
        repository.delivery.next_retry_at = None
        outcomes.append(service.deliver_pending(repository.delivery.id))

    assert outcomes == ["RETRY", "RETRY", "RETRY", "FAILED"]
    assert provider.calls == DELIVERY_MAX_ATTEMPTS
    assert repository.delivery.status == "FAILED"
    assert repository.delivery.attempt == DELIVERY_MAX_ATTEMPTS
    assert repository.delivery.last_error_code == NotificationErrorCode.PROVIDER_FAILED
    assert [delivery_retry_delay_seconds(item) for item in range(1, 5)] == [1, 2, 4, 8]


def test_due_delivery_rescheduling_does_not_call_provider(monkeypatch) -> None:
    repository = MagicMock()
    repository.list_pending_deliveries.return_value = (
        SimpleNamespace(id=11),
        SimpleNamespace(id=12),
    )
    scheduled: list[int] = []
    monkeypatch.setattr(
        "app.service.notification.schedule_notification_delivery",
        lambda settings, delivery_id: scheduled.append(delivery_id) or True,
    )
    service = NotificationService(_session(), repository=repository)

    assert service.schedule_due_deliveries() == 2
    assert service.schedule_due_deliveries() == 2
    assert scheduled == [11, 12, 11, 12]
    assert repository.list_pending_deliveries.call_count == 2
    assert all(
        call.kwargs["limit"] == 100
        and isinstance(call.kwargs["now"], datetime)
        for call in repository.list_pending_deliveries.call_args_list
    )
