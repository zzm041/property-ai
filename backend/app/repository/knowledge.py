"""Knowledge metadata and notification-delivery persistence. No commits."""

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.model.ai import Notification
from app.model.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    NotificationDelivery,
)
from app.model.identity import User, UserWechat


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_document(self, document_id: int) -> KnowledgeDocument | None:
        return self._session.get(KnowledgeDocument, document_id)

    def get_document_for_update(self, document_id: int) -> KnowledgeDocument | None:
        return self._session.scalar(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .with_for_update()
        )

    def get_document_in_tenant(
        self,
        *,
        tenant_id: int,
        document_id: int,
    ) -> KnowledgeDocument | None:
        return self._session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.tenant_id == tenant_id,
            )
        )

    def get_version(
        self,
        *,
        document_id: int,
        version: int,
    ) -> KnowledgeDocumentVersion | None:
        return self._session.scalar(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.document_id == document_id,
                KnowledgeDocumentVersion.version == version,
            )
        )

    def get_ingestion_job(
        self,
        *,
        document_id: int,
        version: int,
    ) -> KnowledgeIngestionJob | None:
        return self._session.scalar(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.document_id == document_id,
                KnowledgeIngestionJob.version == version,
            )
        )

    def get_ingestion_job_for_update(
        self,
        *,
        document_id: int,
        version: int,
    ) -> KnowledgeIngestionJob | None:
        return self._session.scalar(
            select(KnowledgeIngestionJob)
            .where(
                KnowledgeIngestionJob.document_id == document_id,
                KnowledgeIngestionJob.version == version,
            )
            .with_for_update()
        )

    def list_chunks_for_version(
        self,
        *,
        document_id: int,
        version: int,
    ) -> tuple[KnowledgeChunk, ...]:
        return tuple(
            self._session.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.document_id == document_id,
                    KnowledgeChunk.version == version,
                )
                .order_by(KnowledgeChunk.chunk_no)
            ).all()
        )

    def get_chunks_by_ids(self, chunk_ids: tuple[int, ...]) -> tuple[KnowledgeChunk, ...]:
        if not chunk_ids:
            return ()
        rows = self._session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids))
        ).all()
        by_id = {row.id: row for row in rows}
        return tuple(by_id[item] for item in chunk_ids if item in by_id)

    def add_document(self, record: KnowledgeDocument) -> KnowledgeDocument:
        self._session.add(record)
        self._session.flush()
        return record

    def add_version(
        self,
        record: KnowledgeDocumentVersion,
    ) -> KnowledgeDocumentVersion:
        self._session.add(record)
        self._session.flush()
        return record

    def add_job(self, record: KnowledgeIngestionJob) -> KnowledgeIngestionJob:
        self._session.add(record)
        self._session.flush()
        return record

    def add_chunk(self, record: KnowledgeChunk) -> KnowledgeChunk:
        self._session.add(record)
        self._session.flush()
        return record

    def get_user_wechat(
        self,
        *,
        appid: str,
        openid: str,
    ) -> UserWechat | None:
        return self._session.scalar(
            select(UserWechat).where(
                UserWechat.appid == appid,
                UserWechat.openid == openid,
            )
        )

    def get_user_wechat_by_unionid(self, unionid: str) -> UserWechat | None:
        return self._session.scalar(
            select(UserWechat).where(UserWechat.unionid == unionid)
        )

    def next_version_for_update(self, document_id: int) -> int:
        current = self._session.scalar(
            select(KnowledgeDocumentVersion.version)
            .where(KnowledgeDocumentVersion.document_id == document_id)
            .order_by(KnowledgeDocumentVersion.version.desc())
            .limit(1)
            .with_for_update()
        )
        return int(current or 0) + 1

    def list_versions(
        self,
        document_id: int,
    ) -> tuple[KnowledgeDocumentVersion, ...]:
        return tuple(
            self._session.scalars(
                select(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.document_id == document_id)
                .order_by(KnowledgeDocumentVersion.version.desc())
            ).all()
        )

    def list_ingestion_jobs(
        self,
        document_id: int,
    ) -> tuple[KnowledgeIngestionJob, ...]:
        return tuple(
            self._session.scalars(
                select(KnowledgeIngestionJob)
                .where(KnowledgeIngestionJob.document_id == document_id)
                .order_by(KnowledgeIngestionJob.version.desc())
            ).all()
        )

    def list_documents_for_management(
        self,
        *,
        tenant_id: int,
        property_id: int,
        include_tenant_documents: bool,
        include_property_documents: bool,
        include_archived: bool = False,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[KnowledgeDocument, ...]:
        targets = []
        if include_tenant_documents:
            targets.append(KnowledgeDocument.property_id.is_(None))
        if include_property_documents:
            targets.append(KnowledgeDocument.property_id == property_id)
        if not targets:
            return ()
        status_filter = True
        if not include_archived:
            status_filter = KnowledgeDocument.status != "ARCHIVED"
        return tuple(
            self._session.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.tenant_id == tenant_id,
                    KnowledgeDocument.id > after_id,
                    or_(*targets),
                    status_filter,
                )
                .order_by(KnowledgeDocument.id)
                .limit(limit)
            ).all()
        )

    def list_user_wechat_rebind_candidates_for_update(
        self,
        *,
        user_id: int,
        appid: str,
        openid: str,
        unionid: str | None,
    ) -> tuple[UserWechat, ...]:
        target_identity = (
            (UserWechat.appid == appid) & (UserWechat.openid == openid)
        )
        if unionid is not None:
            target_identity = target_identity | (UserWechat.unionid == unionid)
        return tuple(
            self._session.scalars(
                select(UserWechat)
                .where(
                    or_(
                        (UserWechat.user_id == user_id)
                        & (UserWechat.appid == appid),
                        target_identity,
                    )
                )
                .order_by(UserWechat.id)
                .with_for_update()
            ).all()
        )

    def add_user(self, record: User) -> User:
        self._session.add(record)
        self._session.flush()
        return record

    def add_user_wechat(self, record: UserWechat) -> UserWechat:
        self._session.add(record)
        self._session.flush()
        return record

    def list_notifications(
        self,
        *,
        user_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Notification, ...]:
        filters = [Notification.user_id == user_id]
        if after_id > 0:
            filters.append(Notification.id < after_id)
        return tuple(
            self._session.scalars(
                select(Notification)
                .where(*filters)
                .order_by(Notification.id.desc())
                .limit(limit)
            ).all()
        )

    def count_unread(self, user_id: int) -> int:
        from sqlalchemy import func

        value = self._session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        return int(value or 0)

    def get_notification_for_user(
        self,
        *,
        notification_id: int,
        user_id: int,
    ) -> Notification | None:
        return self._session.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .with_for_update()
        )

    def add_notification(self, record: Notification) -> Notification:
        self._session.add(record)
        self._session.flush()
        return record

    def add_delivery(self, record: NotificationDelivery) -> NotificationDelivery:
        self._session.add(record)
        self._session.flush()
        return record

    def list_pending_deliveries(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> tuple[NotificationDelivery, ...]:
        due_at = now
        due_filter = True
        if due_at is not None:
            due_filter = or_(
                NotificationDelivery.next_retry_at.is_(None),
                NotificationDelivery.next_retry_at <= due_at,
            )
        return tuple(
            self._session.scalars(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.status == "PENDING",
                    due_filter,
                )
                .order_by(NotificationDelivery.id)
                .limit(limit)
            ).all()
        )

    def get_delivery(self, delivery_id: int) -> NotificationDelivery | None:
        return self._session.get(NotificationDelivery, delivery_id)

    def claim_pending_delivery(
        self,
        delivery_id: int,
        *,
        now: datetime,
        lease_until: datetime,
        claim_code: str,
    ) -> NotificationDelivery | None:
        result = self._session.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == "PENDING",
                or_(
                    NotificationDelivery.next_retry_at.is_(None),
                    NotificationDelivery.next_retry_at <= now,
                ),
            )
            .values(
                attempt=NotificationDelivery.attempt + 1,
                next_retry_at=lease_until,
                last_error_code=claim_code,
            )
            .execution_options(synchronize_session="fetch")
        )
        if int(result.rowcount or 0) != 1:
            return None
        return self._session.get(
            NotificationDelivery,
            delivery_id,
            populate_existing=True,
        )

    def finish_claimed_delivery(
        self,
        *,
        delivery_id: int,
        attempt: int,
        claim_code: str,
        status: str | None,
        last_error_code: str | None,
        delivered_at: datetime | None,
        next_retry_at: datetime | None,
        update_lease: bool,
    ) -> bool:
        values: dict[str, object] = {"last_error_code": last_error_code}
        if status is not None:
            values["status"] = status
        if delivered_at is not None or status == "SUCCEEDED":
            values["delivered_at"] = delivered_at
        if update_lease:
            values["next_retry_at"] = next_retry_at
        result = self._session.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status == "PENDING",
                NotificationDelivery.attempt == attempt,
                NotificationDelivery.last_error_code == claim_code,
            )
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return int(result.rowcount or 0) == 1

    def get_notification(self, notification_id: int) -> Notification | None:
        return self._session.get(Notification, notification_id)
