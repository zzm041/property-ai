"""In-app notification outbox. External push is best-effort after commit."""

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.errors import AppError, IamErrorCode, NotificationErrorCode
from app.core.time import utc_now
from app.domain.iam import Principal
from app.model.ai import Notification
from app.model.knowledge import NotificationDelivery
from app.provider.errors import ProviderUnavailableError
from app.provider.factory import build_notification_provider
from app.core.config import Settings, get_settings
from app.repository.knowledge import KnowledgeRepository
from app.worker.enqueue import schedule_notification_delivery


class NotificationService:
    def __init__(
        self,
        session: Session,
        repository: KnowledgeRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or KnowledgeRepository(session)
        self._settings = settings or get_settings()

    def enqueue(
        self,
        *,
        tenant_id: int,
        property_id: int,
        user_id: int,
        title: str,
        body: str,
        resource_type: str,
        resource_id: int,
    ) -> Notification:
        record = self._repository.add_notification(
            Notification(
                tenant_id=tenant_id,
                property_id=property_id,
                user_id=user_id,
                title=title[:200],
                body=body[:500],
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
        delivery = self._repository.add_delivery(
            NotificationDelivery(
                notification_id=record.id,
                channel="IN_APP",
                status="PENDING",
            )
        )
        pending = self._session.info.setdefault("pending_notification_deliveries", [])
        pending.append(delivery.id)
        self._session.info["notification_settings"] = self._settings
        return record

    def list_mine(
        self,
        *,
        actor: Principal,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Notification, ...], int]:
        if actor.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        items = self._repository.list_notifications(
            user_id=actor.user_id,
            after_id=max(after_id, 0),
            limit=min(max(limit, 1), 100),
        )
        unread = self._repository.count_unread(actor.user_id)
        return items, unread

    def mark_read(self, *, actor: Principal, notification_id: int) -> Notification:
        from app.core.database import begin_owned_transaction

        with begin_owned_transaction(self._session):
            record = self._repository.get_notification_for_user(
                notification_id=notification_id,
                user_id=actor.user_id,
            )
            if record is None:
                raise AppError(
                    code=NotificationErrorCode.NOT_FOUND,
                    message="Notification was not found",
                    status_code=404,
                )
            if record.read_at is None:
                record.read_at = utc_now()
            return record

    def deliver_pending(self, delivery_id: int) -> None:
        from app.core.database import begin_owned_transaction

        snapshot: dict[str, object] | None = None
        with begin_owned_transaction(self._session):
            delivery = self._repository.get_delivery(delivery_id)
            if delivery is None or delivery.status != "PENDING":
                return
            notification = self._repository.get_notification(delivery.notification_id)
            if notification is None:
                delivery.status = "FAILED"
                delivery.last_error_code = "NTF.NOT_FOUND"
                return
            snapshot = {
                "user_id": notification.user_id,
                "title": notification.title,
                "body": notification.body,
            }
        if snapshot is None:
            return
        provider = build_notification_provider(self._settings)
        push_error: str | None = None
        try:
            provider.deliver_push(
                user_id=int(snapshot["user_id"]),
                title=str(snapshot["title"]),
                body=str(snapshot["body"]),
            )
        except ProviderUnavailableError:
            push_error = "NTF.PROVIDER_FAILED"
        with begin_owned_transaction(self._session):
            delivery = self._repository.get_delivery(delivery_id)
            if delivery is None or delivery.status != "PENDING":
                return
            delivery.attempt += 1
            if push_error is not None:
                delivery.status = "FAILED"
                delivery.last_error_code = push_error
                return
            delivery.status = "SUCCEEDED"
            delivery.delivered_at = utc_now()


@event.listens_for(Session, "after_commit")
def _schedule_notification_deliveries(session: Session) -> None:
    pending = session.info.pop("pending_notification_deliveries", None)
    if not pending:
        return
    settings = session.info.get("notification_settings") or get_settings()
    for delivery_id in pending:
        schedule_notification_delivery(settings, delivery_id)


@event.listens_for(Session, "after_rollback")
def _clear_pending_notification_deliveries(session: Session) -> None:
    session.info.pop("pending_notification_deliveries", None)
