"""Persistence and deterministic locks for the 0014 identity workflow."""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.model.ai import Notification
from app.model.employee import Employee
from app.model.identity import (
    AccountMerge,
    AuthSession,
    IdentityRecoveryCase,
    IdentityRecoveryReview,
    PasswordResetTicket,
    PhoneIdentityTombstone,
    User,
    UserPassword,
    UserPhone,
    UserRole,
    UserWechat,
)
from app.model.knowledge import NotificationDelivery
from app.model.resident import ResidentBindRequest, ResidentHouseRelation
from app.model.work_order import FileObject, WorkOrder


@dataclass(frozen=True, slots=True)
class IdentityCompletionRows:
    users: tuple[User, ...]
    phones: tuple[UserPhone, ...]
    passwords: tuple[UserPassword, ...]
    sessions: tuple[AuthSession, ...]
    reset_tickets: tuple[PasswordResetTicket, ...]
    wechats: tuple[UserWechat, ...]
    roles: tuple[UserRole, ...]
    employees: tuple[Employee, ...]
    relations: tuple[ResidentHouseRelation, ...]
    pending_bind_requests: tuple[ResidentBindRequest, ...]
    files: tuple[FileObject, ...]
    notifications: tuple[Notification, ...]
    pending_deliveries: tuple[NotificationDelivery, ...]
    work_orders: tuple[WorkOrder, ...]
    tombstones: tuple[PhoneIdentityTombstone, ...]
    merges: tuple[AccountMerge, ...]


class IdentityRecoveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_case(self, record: IdentityRecoveryCase) -> IdentityRecoveryCase:
        self._session.add(record)
        self._session.flush()
        return record

    def get_case(self, case_id: int) -> IdentityRecoveryCase | None:
        return self._session.get(IdentityRecoveryCase, case_id)

    def get_case_for_update(self, case_id: int) -> IdentityRecoveryCase | None:
        return self._session.scalar(
            select(IdentityRecoveryCase)
            .where(IdentityRecoveryCase.id == case_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def find_active_case(self, *, purpose: str, phone_e164: str) -> IdentityRecoveryCase | None:
        return self._session.scalar(
            select(IdentityRecoveryCase)
            .where(
                IdentityRecoveryCase.purpose == purpose,
                IdentityRecoveryCase.active_subject_key == phone_e164,
            )
            .with_for_update()
        )

    def list_cases(self, *, status: str | None, after_id: int, limit: int) -> tuple[IdentityRecoveryCase, ...]:
        query = select(IdentityRecoveryCase).where(IdentityRecoveryCase.id > after_id)
        if status is not None:
            query = query.where(IdentityRecoveryCase.status == status)
        return tuple(self._session.scalars(query.order_by(IdentityRecoveryCase.id).limit(limit)).all())

    def add_review(self, record: IdentityRecoveryReview) -> IdentityRecoveryReview:
        self._session.add(record)
        self._session.flush()
        return record

    def list_reviews(self, case_id: int) -> tuple[IdentityRecoveryReview, ...]:
        return tuple(
            self._session.scalars(
                select(IdentityRecoveryReview)
                .where(IdentityRecoveryReview.case_id == case_id)
                .order_by(IdentityRecoveryReview.id)
            ).all()
        )

    def latest_phone_owner(self, phone_e164: str) -> UserPhone | None:
        return self._session.scalar(
            select(UserPhone)
            .where(UserPhone.phone_e164 == phone_e164)
            .order_by(UserPhone.id.desc())
            .limit(1)
        )

    def list_requester_cases(self, *, user_id: int, after_id: int, limit: int) -> tuple[IdentityRecoveryCase, ...]:
        return tuple(
            self._session.scalars(
                select(IdentityRecoveryCase)
                .where(
                    IdentityRecoveryCase.requester_user_id == user_id,
                    IdentityRecoveryCase.id > after_id,
                )
                .order_by(IdentityRecoveryCase.id)
                .limit(limit)
            ).all()
        )

    def lock_completion_rows(
        self, *, source_user_id: int, target_user_id: int, phone_e164: str
    ) -> IdentityCompletionRows:
        user_ids = tuple(sorted({source_user_id, target_user_id}))

        def rows(model, predicate, *, order_by=None):
            statement = select(model).where(predicate)
            if order_by is not None:
                statement = statement.order_by(order_by)
            return tuple(
                self._session.scalars(
                    statement.execution_options(populate_existing=True).with_for_update()
                ).all()
            )

        users = rows(User, User.id.in_(user_ids), order_by=User.id)
        phones = rows(
            UserPhone,
            or_(UserPhone.user_id.in_(user_ids), UserPhone.phone_e164 == phone_e164),
            order_by=UserPhone.id,
        )
        passwords = rows(UserPassword, UserPassword.user_id.in_(user_ids), order_by=UserPassword.id)
        sessions = rows(AuthSession, AuthSession.user_id.in_(user_ids), order_by=AuthSession.id)
        reset_tickets = rows(
            PasswordResetTicket,
            PasswordResetTicket.user_id.in_(user_ids),
            order_by=PasswordResetTicket.id,
        )
        wechats = rows(UserWechat, UserWechat.user_id.in_(user_ids), order_by=UserWechat.id)
        roles = rows(UserRole, UserRole.user_id.in_(user_ids), order_by=UserRole.id)
        employees = rows(Employee, Employee.user_id.in_(user_ids), order_by=Employee.id)
        relations = rows(
            ResidentHouseRelation,
            ResidentHouseRelation.user_id.in_(user_ids),
            order_by=ResidentHouseRelation.id,
        )
        pending_bind_requests = rows(
            ResidentBindRequest,
            ResidentBindRequest.user_id.in_(user_ids),
            order_by=ResidentBindRequest.id,
        )
        files = rows(FileObject, FileObject.uploader_user_id.in_(user_ids), order_by=FileObject.id)
        notifications = rows(Notification, Notification.user_id.in_(user_ids), order_by=Notification.id)
        source_notification_ids = tuple(item.id for item in notifications if item.user_id == source_user_id)
        pending_deliveries = (
            rows(
                NotificationDelivery,
                NotificationDelivery.notification_id.in_(source_notification_ids),
                order_by=NotificationDelivery.id,
            )
            if source_notification_ids
            else ()
        )
        work_orders = rows(WorkOrder, WorkOrder.reporter_user_id.in_(user_ids), order_by=WorkOrder.id)
        tombstones = rows(
            PhoneIdentityTombstone,
            PhoneIdentityTombstone.phone_e164 == phone_e164,
            order_by=PhoneIdentityTombstone.id,
        )
        merges = rows(
            AccountMerge,
            or_(
                AccountMerge.source_user_id.in_(user_ids),
                AccountMerge.target_user_id.in_(user_ids),
            ),
            order_by=AccountMerge.id,
        )
        return IdentityCompletionRows(
            users=users,
            phones=phones,
            passwords=passwords,
            sessions=sessions,
            reset_tickets=reset_tickets,
            wechats=wechats,
            roles=roles,
            employees=employees,
            relations=relations,
            pending_bind_requests=pending_bind_requests,
            files=files,
            notifications=notifications,
            pending_deliveries=pending_deliveries,
            work_orders=work_orders,
            tombstones=tombstones,
            merges=merges,
        )

    def add_merge(self, record: AccountMerge) -> AccountMerge:
        self._session.add(record)
        self._session.flush()
        return record

    def add_tombstone(self, record: PhoneIdentityTombstone) -> PhoneIdentityTombstone:
        self._session.add(record)
        self._session.flush()
        return record

    def add_phone(self, record: UserPhone) -> UserPhone:
        self._session.add(record)
        self._session.flush()
        return record

    def add_password(self, record: UserPassword) -> UserPassword:
        self._session.add(record)
        self._session.flush()
        return record

    def delete_password(self, record: UserPassword) -> None:
        self._session.delete(record)

    def delete_role(self, record: UserRole) -> None:
        self._session.delete(record)
