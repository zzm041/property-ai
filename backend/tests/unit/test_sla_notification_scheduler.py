"""SLA state idempotency and bounded notification-delivery retries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.errors import NotificationErrorCode
from app.provider.errors import ProviderUnavailableError
from app.service.notification import (
    DELIVERY_MAX_ATTEMPTS,
    NotificationService,
    delivery_retry_delay_seconds,
)
from app.service.sla import SlaService


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
    monkeypatch.setattr("app.service.sla.NotificationService", _Notifier)
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
    monkeypatch.setattr("app.service.sla.NotificationService", _Notifier)

    result = SlaService(_session(), repository=repository).sweep_due(now=now)

    assert result.stopped == 1
    assert result.notifications == 0
    assert clock.state == "STOPPED"
    assert repository.logs == []


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
