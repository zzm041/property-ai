"""Notification event recipients, inbox keyset paging, and safe page wiring."""

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mysql

from app.api.dependencies.auth import get_current_principal
from app.api.v1.notifications import (
    get_notification_service,
    list_notifications,
    router,
)
from app.core.errors import AppError, IamErrorCode, NotificationErrorCode
from app.domain.iam import Principal
from app.repository.knowledge import KnowledgeRepository
from app.service.notification import NotificationService
from app.service.work_order import WorkOrderService


NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _actor(*, status: str = "ACTIVE", user_id: int = 7) -> Principal:
    return Principal(
        user_id=user_id,
        status=status,
        display_name="Synthetic notification user",
        grants=(),
    )


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/notifications",
            "headers": [],
        }
    )
    request.state.request_id = "ntf013-unit"
    return request


def _notification(record_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        tenant_id=1,
        property_id=2,
        user_id=7,
        title=f"notification {record_id}",
        body="body",
        resource_type="WORK_ORDER",
        resource_id=41,
        read_at=None,
        created_at=NOW,
    )


def _session() -> MagicMock:
    session = MagicMock()
    session.info = {}
    session.in_transaction.return_value = False
    session.begin.return_value = nullcontext()
    return session


def test_repository_uses_descending_keyset_cursor() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = KnowledgeRepository(session)

    repository.list_notifications(user_id=7, after_id=0, limit=21)
    first = session.scalars.call_args.args[0]
    first_sql = str(
        first.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "notifications.user_id = 7" in first_sql
    assert "notifications.id <" not in first_sql
    assert "ORDER BY notifications.id DESC" in first_sql
    assert "LIMIT 21" in first_sql

    repository.list_notifications(user_id=7, after_id=81, limit=21)
    continued = session.scalars.call_args.args[0]
    continued_sql = str(
        continued.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "notifications.user_id = 7" in continued_sql
    assert "notifications.id < 81" in continued_sql
    assert "notifications.id > 81" not in continued_sql
    assert "ORDER BY notifications.id DESC" in continued_sql


@pytest.mark.parametrize(
    ("count", "expected_cursor"),
    ((21, 81), (20, None), (7, None), (0, None)),
)
def test_service_lookahead_has_exact_final_boundary(
    count: int,
    expected_cursor: int | None,
) -> None:
    repository = MagicMock()
    repository.list_notifications.return_value = tuple(
        _notification(record_id) for record_id in range(100, 100 - count, -1)
    )
    repository.count_unread.return_value = count
    service = NotificationService(_session(), repository=repository)

    items, unread, next_cursor = service.list_mine(
        actor=_actor(),
        after_id=0,
        limit=20,
    )

    assert len(items) == min(count, 20)
    assert unread == count
    assert next_cursor == expected_cursor
    repository.list_notifications.assert_called_once_with(
        user_id=7,
        after_id=0,
        limit=21,
    )


def test_route_returns_scope_fields_and_service_cursor() -> None:
    service = MagicMock()
    service.list_mine.return_value = ((_notification(100), _notification(99)), 2, 99)

    payload = list_notifications(
        _request(),
        _actor(),
        service,
        after_id=0,
        limit=2,
    )["data"]

    assert [item["id"] for item in payload["items"]] == [100, 99]
    assert payload["items"][0]["tenant_id"] == 1
    assert payload["items"][0]["property_id"] == 2
    assert payload["next_cursor"] == 99


@pytest.mark.parametrize("query", ("after_id=-1", "limit=0", "limit=101"))
def test_query_bounds_return_422_before_service(query: str) -> None:
    service = MagicMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_principal] = _actor
    app.dependency_overrides[get_notification_service] = lambda: service

    response = TestClient(app).get(f"/notifications?{query}")

    assert response.status_code == 422
    service.list_mine.assert_not_called()


def test_list_and_mark_read_reject_inactive_actor() -> None:
    repository = MagicMock()
    service = NotificationService(_session(), repository=repository)
    actor = _actor(status="DISABLED")

    with pytest.raises(AppError) as listed:
        service.list_mine(actor=actor)
    with pytest.raises(AppError) as marked:
        service.mark_read(actor=actor, notification_id=1)

    assert listed.value.code == IamErrorCode.FORBIDDEN
    assert marked.value.code == IamErrorCode.FORBIDDEN
    repository.list_notifications.assert_not_called()
    repository.get_notification_for_user.assert_not_called()


def test_mark_read_is_anti_idor_and_repeat_is_idempotent(monkeypatch) -> None:
    repository = MagicMock()
    service = NotificationService(_session(), repository=repository)
    repository.get_notification_for_user.return_value = None

    with pytest.raises(AppError) as hidden:
        service.mark_read(actor=_actor(), notification_id=900)
    assert hidden.value.code == NotificationErrorCode.NOT_FOUND
    assert hidden.value.status_code == 404
    repository.get_notification_for_user.assert_called_once_with(
        notification_id=900,
        user_id=7,
    )

    record = _notification(100)
    repository.get_notification_for_user.return_value = record
    times = iter(
        (
            datetime(2026, 9, 22, 8, 1, tzinfo=UTC),
            datetime(2026, 9, 22, 8, 2, tzinfo=UTC),
        )
    )
    monkeypatch.setattr("app.service.notification.utc_now", lambda: next(times))
    first = service.mark_read(actor=_actor(), notification_id=100)
    second = service.mark_read(actor=_actor(), notification_id=100)
    assert first is second is record
    assert record.read_at == datetime(2026, 9, 22, 8, 1, tzinfo=UTC)


def test_mark_read_repository_locks_owned_notification() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    repository = KnowledgeRepository(session)

    repository.get_notification_for_user(notification_id=100, user_id=7)

    statement = session.scalar.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "notifications.id = 100" in sql
    assert "notifications.user_id = 7" in sql
    assert "FOR UPDATE" in sql


def test_work_order_recipient_helper_deduplicates_and_scopes() -> None:
    service = WorkOrderService.__new__(WorkOrderService)
    service._notify = MagicMock()
    record = SimpleNamespace(id=41, tenant_id=1, property_id=2)

    service._notify_users(
        record,
        user_ids=(7, None, 7, 8, 9),
        exclude_user_ids=(8,),
        title="工单状态更新",
        body="请查看详情",
    )

    assert [call.kwargs["user_id"] for call in service._notify.call_args_list] == [7, 9]
    assert all(
        call.kwargs["tenant_id"] == 1
        and call.kwargs["property_id"] == 2
        and call.kwargs["resource_type"] == "WORK_ORDER"
        and call.kwargs["resource_id"] == 41
        for call in service._notify.call_args_list
    )


def test_miniprogram_inbox_has_paging_read_guard_and_allowlisted_routes() -> None:
    api_source = (REPO_ROOT / "miniprogram/services/api.js").read_text(
        encoding="utf-8"
    )
    page_source = (
        REPO_ROOT / "miniprogram/pages/notifications/list.js"
    ).read_text(encoding="utf-8")
    route_source = (
        REPO_ROOT / "miniprogram/utils/notification-route.js"
    ).read_text(encoding="utf-8")

    assert "notifications: (afterId, limit = 20)" in api_source
    assert "nextCursor" in page_source
    assert "loadMore" in page_source
    assert "openingId" in page_source
    assert "api.readNotification(id)" in page_source
    assert "api.workOrder(resource.resourceId)" in page_source
    assert 'resource.type === "ANNOUNCEMENT"' in route_source
    assert 'resource.type === "RESIDENT_BIND_REQUEST"' in route_source
    assert 'resource.type !== "WORK_ORDER"' in route_source
    assert "notification.resource_type" in route_source
    assert "resource_type}`" not in route_source
