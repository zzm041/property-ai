"""Scoped persistence for resident bind requests and house relations."""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.model.property import House
from app.model.resident import ResidentBindRequest, ResidentHouseRelation


class ResidentBindingRepository:
    """Query/add/flush only. Transaction commit belongs to Service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_registered_house(self, house_id: int) -> House | None:
        return self._session.get(House, house_id)

    def get_house_in_scope(
        self,
        *,
        tenant_id: int,
        property_id: int,
        house_id: int,
    ) -> House | None:
        return self._session.scalar(
            select(House).where(
                House.id == house_id,
                House.tenant_id == tenant_id,
                House.property_id == property_id,
            )
        )

    def get_bind_request(self, request_id: int) -> ResidentBindRequest | None:
        return self._session.get(ResidentBindRequest, request_id)

    def get_bind_request_for_update(
        self,
        request_id: int,
    ) -> ResidentBindRequest | None:
        return self._session.scalar(
            select(ResidentBindRequest)
            .where(ResidentBindRequest.id == request_id)
            .with_for_update()
        )

    def get_bind_request_for_user(
        self,
        *,
        request_id: int,
        user_id: int,
    ) -> ResidentBindRequest | None:
        return self._session.scalar(
            select(ResidentBindRequest).where(
                ResidentBindRequest.id == request_id,
                ResidentBindRequest.user_id == user_id,
            )
        )

    def list_bind_requests_for_user(
        self,
        *,
        user_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[ResidentBindRequest, ...]:
        return tuple(
            self._session.scalars(
                select(ResidentBindRequest)
                .where(
                    ResidentBindRequest.user_id == user_id,
                    ResidentBindRequest.id > after_id,
                )
                .order_by(ResidentBindRequest.id)
                .limit(limit)
            ).all()
        )

    def list_pending_requests_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[ResidentBindRequest, ...]:
        return tuple(
            self._session.scalars(
                select(ResidentBindRequest)
                .where(
                    ResidentBindRequest.tenant_id == tenant_id,
                    ResidentBindRequest.property_id == property_id,
                    ResidentBindRequest.status == "PENDING",
                    ResidentBindRequest.id > after_id,
                )
                .order_by(ResidentBindRequest.id)
                .limit(limit)
            ).all()
        )

    def find_active_relation(
        self,
        *,
        user_id: int,
        house_id: int,
        now: datetime,
    ) -> ResidentHouseRelation | None:
        return self._session.scalar(
            select(ResidentHouseRelation).where(
                ResidentHouseRelation.user_id == user_id,
                ResidentHouseRelation.house_id == house_id,
                ResidentHouseRelation.status == "ACTIVE",
                ResidentHouseRelation.valid_from <= now,
                or_(
                    ResidentHouseRelation.valid_to.is_(None),
                    ResidentHouseRelation.valid_to > now,
                ),
            )
        )

    def get_active_house_for_user(
        self,
        *,
        user_id: int,
        house_id: int,
        now: datetime,
    ) -> House | None:
        return self._session.scalar(
            select(House)
            .join(
                ResidentHouseRelation,
                (ResidentHouseRelation.house_id == House.id)
                & (ResidentHouseRelation.tenant_id == House.tenant_id)
                & (ResidentHouseRelation.property_id == House.property_id),
            )
            .where(
                ResidentHouseRelation.user_id == user_id,
                House.id == house_id,
                ResidentHouseRelation.status == "ACTIVE",
                ResidentHouseRelation.valid_from <= now,
                or_(
                    ResidentHouseRelation.valid_to.is_(None),
                    ResidentHouseRelation.valid_to > now,
                ),
            )
        )

    def list_active_relations_for_user(
        self,
        *,
        user_id: int,
        now: datetime,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[ResidentHouseRelation, House], ...]:
        rows = self._session.execute(
            select(ResidentHouseRelation, House)
            .join(
                House,
                (House.id == ResidentHouseRelation.house_id)
                & (House.tenant_id == ResidentHouseRelation.tenant_id)
                & (House.property_id == ResidentHouseRelation.property_id),
            )
            .where(
                ResidentHouseRelation.user_id == user_id,
                ResidentHouseRelation.status == "ACTIVE",
                ResidentHouseRelation.valid_from <= now,
                or_(
                    ResidentHouseRelation.valid_to.is_(None),
                    ResidentHouseRelation.valid_to > now,
                ),
                ResidentHouseRelation.id > after_id,
            )
            .order_by(ResidentHouseRelation.id)
            .limit(limit)
        ).all()
        return tuple((relation, house) for relation, house in rows)

    def add_bind_request(
        self,
        request: ResidentBindRequest,
    ) -> ResidentBindRequest:
        self._session.add(request)
        self._session.flush()
        return request

    def add_relation(
        self,
        relation: ResidentHouseRelation,
    ) -> ResidentHouseRelation:
        self._session.add(relation)
        self._session.flush()
        return relation

    def end_relation(
        self,
        relation: ResidentHouseRelation,
        *,
        ended_at: datetime,
        status: str,
    ) -> ResidentHouseRelation:
        relation.valid_to = ended_at
        relation.status = status
        self._session.flush()
        return relation
