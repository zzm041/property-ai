"""In-app notification outbox. External push is best-effort after commit."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError, IamErrorCode, NotificationErrorCode
from app.core.logging import get_logger
from app.core.time import utc_now
from app.domain.iam import Principal
from app.model.ai import Notification
from app.model.knowledge import NotificationDelivery
from app.provider.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.factory import build_notification_provider
from app.repository.knowledge import KnowledgeRepository
from app.worker.enqueue import schedule_notification_delivery

logger = get_logger(__name__)

DELIVERY_CLAIM_SECONDS = 120
DELIVERY_CLAIM_PREFIX = "NTF.CLAIM:"
DELIVERY_MAX_ATTEMPTS = 4


def delivery_retry_delay_seconds(attempt: int) -> int:
    return min(2 ** max(attempt - 1, 0), 30)


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

    def deliver_pending(self, delivery_id: int) -> str:
        """Claim one PENDING row, push outside the transaction, then write back.

        Status stays PENDING|SUCCEEDED|FAILED (0007 CHECK). Claim uses attempt
        plus next_retry_at as a lease. DevelopmentNotificationProvider has no
        vendor idempotency key: a crash after the provider accepted the push
        but before local write-back can cause a second push after lease expiry.
        That window is at-least-once, not exactly-once.
        """

        snapshot = self._claim_delivery(delivery_id)
        if snapshot is None:
            return self._delivery_outcome(delivery_id)
        if snapshot.get("terminal"):
            return str(snapshot["terminal"])
        if self._session.in_transaction():
            from app.core.database import TransactionContractError

            raise TransactionContractError(
                "Provider calls must not hold a business transaction"
            )
        provider = build_notification_provider(self._settings)
        outcome = "SUCCEEDED"
        error_code: str | None = None
        try:
            provider.deliver_push(
                user_id=int(snapshot["user_id"]),
                title=str(snapshot["title"]),
                body=str(snapshot["body"]),
            )
        except (ProviderTimeoutError, ProviderUnavailableError, ProviderRateLimitError):
            outcome = (
                "FAILED"
                if int(snapshot["attempt"]) >= DELIVERY_MAX_ATTEMPTS
                else "RETRY"
            )
            error_code = NotificationErrorCode.PROVIDER_FAILED
        except Exception:
            outcome = (
                "FAILED"
                if int(snapshot["attempt"]) >= DELIVERY_MAX_ATTEMPTS
                else "UNCERTAIN"
            )
            error_code = NotificationErrorCode.PROVIDER_UNCERTAIN
            logger.warning(
                "notification_push_uncertain",
                delivery_id=delivery_id,
                attempt=snapshot["attempt"],
            )
        self._finish_delivery(snapshot, outcome, error_code)
        return outcome

    def _claim_delivery(self, delivery_id: int) -> dict[str, object] | None:
        from app.core.database import begin_owned_transaction

        with begin_owned_transaction(self._session):
            now = utc_now()
            claim_code = f"{DELIVERY_CLAIM_PREFIX}{uuid4().hex}"
            delivery = self._repository.claim_pending_delivery(
                delivery_id,
                now=now,
                lease_until=now + timedelta(seconds=DELIVERY_CLAIM_SECONDS),
                claim_code=claim_code,
            )
            if delivery is None:
                return None
            notification = self._repository.get_notification(delivery.notification_id)
            if notification is None:
                self._repository.finish_claimed_delivery(
                    delivery_id=delivery.id,
                    attempt=delivery.attempt,
                    claim_code=claim_code,
                    status="FAILED",
                    last_error_code=NotificationErrorCode.NOT_FOUND,
                    delivered_at=None,
                    next_retry_at=None,
                    update_lease=True,
                )
                return {"terminal": "FAILED"}
            return {
                "delivery_id": delivery.id,
                "attempt": delivery.attempt,
                "claim_code": claim_code,
                "user_id": notification.user_id,
                "title": notification.title,
                "body": notification.body,
            }

    def _finish_delivery(
        self,
        snapshot: dict[str, object],
        outcome: str,
        error_code: str | None,
    ) -> None:
        from app.core.database import begin_owned_transaction

        with begin_owned_transaction(self._session):
            if outcome == "SUCCEEDED":
                self._repository.finish_claimed_delivery(
                    delivery_id=int(snapshot["delivery_id"]),
                    attempt=int(snapshot["attempt"]),
                    claim_code=str(snapshot["claim_code"]),
                    status="SUCCEEDED",
                    last_error_code=None,
                    delivered_at=utc_now(),
                    next_retry_at=None,
                    update_lease=True,
                )
                return
            if outcome == "FAILED":
                self._repository.finish_claimed_delivery(
                    delivery_id=int(snapshot["delivery_id"]),
                    attempt=int(snapshot["attempt"]),
                    claim_code=str(snapshot["claim_code"]),
                    status="FAILED",
                    last_error_code=error_code,
                    delivered_at=None,
                    next_retry_at=None,
                    update_lease=True,
                )
                return
            if outcome == "RETRY":
                attempt = int(snapshot["attempt"])
                self._repository.finish_claimed_delivery(
                    delivery_id=int(snapshot["delivery_id"]),
                    attempt=attempt,
                    claim_code=str(snapshot["claim_code"]),
                    status=None,
                    last_error_code=error_code,
                    delivered_at=None,
                    next_retry_at=utc_now()
                    + timedelta(seconds=delivery_retry_delay_seconds(attempt)),
                    update_lease=True,
                )
                return
            self._repository.finish_claimed_delivery(
                delivery_id=int(snapshot["delivery_id"]),
                attempt=int(snapshot["attempt"]),
                claim_code=str(snapshot["claim_code"]),
                status=None,
                last_error_code=error_code,
                delivered_at=None,
                next_retry_at=None,
                update_lease=False,
            )

    def _delivery_outcome(self, delivery_id: int) -> str:
        delivery = self._repository.get_delivery(delivery_id)
        if delivery is None:
            return "SKIPPED"
        if delivery.status in {"SUCCEEDED", "FAILED"}:
            return str(delivery.status)
        return "SKIPPED"

    def schedule_due_deliveries(self, *, limit: int = 100) -> int:
        deliveries = self._repository.list_pending_deliveries(
            now=utc_now(),
            limit=min(max(limit, 1), 500),
        )
        scheduled = 0
        for delivery in deliveries:
            if schedule_notification_delivery(self._settings, delivery.id):
                scheduled += 1
        return scheduled


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
