"""Scoped persistence for resident bind requests and house relations."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.model.identity import User
from app.model.property import Building, House, Property, Unit
from app.model.resident import ResidentBindRequest, ResidentHouseRelation


@dataclass(frozen=True)
class ResidentBindReviewDetails:
    applicant_display_name: str
    property_name: str
    building_name: str
    unit_name: str
    room_no: str


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
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def get_relation_for_update(
        self,
        relation_id: int,
    ) -> ResidentHouseRelation | None:
        return self._session.scalar(
            select(ResidentHouseRelation)
            .where(ResidentHouseRelation.id == relation_id)
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

    def has_active_property_relation(
        self, *, user_id: int, tenant_id: int, property_id: int, now: datetime,
    ) -> bool:
        """Check the requested scope without truncating a user's relation list."""
        return self._session.scalar(
            select(ResidentHouseRelation.id)
            .join(House, (House.id == ResidentHouseRelation.house_id)
                  & (House.tenant_id == ResidentHouseRelation.tenant_id)
                  & (House.property_id == ResidentHouseRelation.property_id))
            .where(
                ResidentHouseRelation.user_id == user_id,
                ResidentHouseRelation.tenant_id == tenant_id,
                ResidentHouseRelation.property_id == property_id,
                ResidentHouseRelation.status == "ACTIVE",
                ResidentHouseRelation.valid_from <= now,
                or_(ResidentHouseRelation.valid_to.is_(None), ResidentHouseRelation.valid_to > now),
            ).limit(1)
        ) is not None

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

    def list_active_resident_user_ids(
        self,
        *,
        tenant_id: int,
        property_id: int,
        now: datetime,
    ) -> tuple[int, ...]:
        rows = self._session.scalars(
            select(ResidentHouseRelation.user_id)
            .where(
                ResidentHouseRelation.tenant_id == tenant_id,
                ResidentHouseRelation.property_id == property_id,
                ResidentHouseRelation.status == "ACTIVE",
                ResidentHouseRelation.valid_from <= now,
                or_(
                    ResidentHouseRelation.valid_to.is_(None),
                    ResidentHouseRelation.valid_to > now,
                ),
            )
            .distinct()
        ).all()
        return tuple(int(item) for item in rows)

    def list_active_resident_targets(
        self,
        *,
        tenant_id: int,
        property_id: int | None,
        now: datetime,
    ) -> tuple[tuple[ResidentHouseRelation, House], ...]:
        conditions = [
            ResidentHouseRelation.tenant_id == tenant_id,
            ResidentHouseRelation.status == "ACTIVE",
            ResidentHouseRelation.valid_from <= now,
            or_(
                ResidentHouseRelation.valid_to.is_(None),
                ResidentHouseRelation.valid_to > now,
            ),
        ]
        if property_id is not None:
            conditions.append(ResidentHouseRelation.property_id == property_id)
        rows = self._session.execute(
            select(ResidentHouseRelation, House)
            .join(
                House,
                (House.id == ResidentHouseRelation.house_id)
                & (House.tenant_id == ResidentHouseRelation.tenant_id)
                & (House.property_id == ResidentHouseRelation.property_id),
            )
            .where(*conditions)
            .order_by(ResidentHouseRelation.id)
        ).all()
        return tuple((relation, house) for relation, house in rows)

    def list_relation_history_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
        after_id: int = 0,
        limit: int = 50,
        house_id: int | None = None,
        user_id: int | None = None,
    ) -> tuple[tuple[ResidentHouseRelation, House, User], ...]:
        statement = (
            select(ResidentHouseRelation, House, User)
            .join(
                House,
                (House.id == ResidentHouseRelation.house_id)
                & (House.tenant_id == ResidentHouseRelation.tenant_id)
                & (House.property_id == ResidentHouseRelation.property_id),
            )
            .join(User, User.id == ResidentHouseRelation.user_id)
            .where(
                ResidentHouseRelation.tenant_id == tenant_id,
                ResidentHouseRelation.property_id == property_id,
                ResidentHouseRelation.id > after_id,
            )
        )
        if house_id is not None:
            statement = statement.where(ResidentHouseRelation.house_id == house_id)
        if user_id is not None:
            statement = statement.where(ResidentHouseRelation.user_id == user_id)
        rows = self._session.execute(
            statement.order_by(ResidentHouseRelation.id).limit(limit)
        ).all()
        return tuple((relation, house, user) for relation, house, user in rows)

    def get_review_details_for_requests(
        self,
        *,
        tenant_id: int,
        property_id: int,
        request_ids: tuple[int, ...],
    ) -> dict[int, ResidentBindReviewDetails]:
        if not request_ids:
            return {}
        rows = self._session.execute(
            select(
                ResidentBindRequest.id,
                User.display_name,
                Property.name,
                Building.name,
                Unit.name,
                House.room_no,
            )
            .join(User, User.id == ResidentBindRequest.user_id)
            .join(
                House,
                and_(
                    House.id == ResidentBindRequest.house_id,
                    House.tenant_id == ResidentBindRequest.tenant_id,
                    House.property_id == ResidentBindRequest.property_id,
                ),
            )
            .join(
                Unit,
                and_(
                    Unit.id == House.unit_id,
                    Unit.tenant_id == House.tenant_id,
                    Unit.property_id == House.property_id,
                ),
            )
            .join(
                Building,
                and_(
                    Building.id == Unit.building_id,
                    Building.tenant_id == Unit.tenant_id,
                    Building.property_id == Unit.property_id,
                ),
            )
            .join(
                Property,
                and_(
                    Property.id == House.property_id,
                    Property.tenant_id == House.tenant_id,
                ),
            )
            .where(
                ResidentBindRequest.tenant_id == tenant_id,
                ResidentBindRequest.property_id == property_id,
                ResidentBindRequest.id.in_(request_ids),
            )
        ).all()
        return {
            request_id: ResidentBindReviewDetails(
                applicant_display_name=applicant_display_name,
                property_name=property_name,
                building_name=building_name,
                unit_name=unit_name,
                room_no=room_no,
            )
            for (
                request_id,
                applicant_display_name,
                property_name,
                building_name,
                unit_name,
                room_no,
            ) in rows
        }

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
