"""Persistence for the pending 0014 high-risk identity workflow."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.identity import IdentityRecoveryCase, IdentityRecoveryReview, UserPhone


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
