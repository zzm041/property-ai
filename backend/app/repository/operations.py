"""Read-only scoped aggregate queries for the operations dashboard."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.model.ai import Incident
from app.model.employee import Employee, EmployeeDailyStatus, EmployeeProperty
from app.model.work_order import SlaClock, WorkOrder


GroupedCountRow = tuple[int, str, int]
StaffingRow = tuple[int, int, int, int, int, int]


class OperationsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _property_ids(property_ids: Sequence[int]) -> tuple[int, ...]:
        return tuple(sorted(set(property_ids)))

    def list_work_order_status_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        return self._list_work_order_group_counts(
            WorkOrder.status,
            tenant_id=tenant_id,
            property_ids=property_ids,
        )

    def list_work_order_category_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        return self._list_work_order_group_counts(
            WorkOrder.category,
            tenant_id=tenant_id,
            property_ids=property_ids,
        )

    def list_work_order_priority_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        return self._list_work_order_group_counts(
            WorkOrder.priority,
            tenant_id=tenant_id,
            property_ids=property_ids,
        )

    def _list_work_order_group_counts(
        self,
        dimension,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        scoped_ids = self._property_ids(property_ids)
        if not scoped_ids:
            return ()
        rows = self._session.execute(
            select(WorkOrder.property_id, dimension, func.count(WorkOrder.id))
            .where(
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.property_id.in_(scoped_ids),
            )
            .group_by(WorkOrder.property_id, dimension)
            .order_by(WorkOrder.property_id, dimension)
        ).all()
        return tuple((int(row[0]), str(row[1]), int(row[2])) for row in rows)

    def list_incident_status_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        scoped_ids = self._property_ids(property_ids)
        if not scoped_ids:
            return ()
        rows = self._session.execute(
            select(Incident.property_id, Incident.status, func.count(Incident.id))
            .where(
                Incident.tenant_id == tenant_id,
                Incident.property_id.in_(scoped_ids),
            )
            .group_by(Incident.property_id, Incident.status)
            .order_by(Incident.property_id, Incident.status)
        ).all()
        return tuple((int(row[0]), str(row[1]), int(row[2])) for row in rows)

    def list_sla_clock_state_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
    ) -> tuple[GroupedCountRow, ...]:
        scoped_ids = self._property_ids(property_ids)
        if not scoped_ids:
            return ()
        rows = self._session.execute(
            select(WorkOrder.property_id, SlaClock.state, func.count(SlaClock.id))
            .join(
                WorkOrder,
                and_(
                    WorkOrder.id == SlaClock.work_order_id,
                    WorkOrder.tenant_id == SlaClock.tenant_id,
                    WorkOrder.property_id == SlaClock.property_id,
                ),
            )
            .where(
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.property_id.in_(scoped_ids),
            )
            .group_by(WorkOrder.property_id, SlaClock.state)
            .order_by(WorkOrder.property_id, SlaClock.state)
        ).all()
        return tuple((int(row[0]), str(row[1]), int(row[2])) for row in rows)

    def list_active_staff_assignment_counts(
        self,
        *,
        tenant_id: int,
        property_ids: Sequence[int],
        at: datetime,
    ) -> tuple[tuple[int, int], ...]:
        scoped_ids = self._property_ids(property_ids)
        if not scoped_ids:
            return ()
        rows = self._session.execute(
            select(
                EmployeeProperty.property_id,
                func.count(func.distinct(EmployeeProperty.employee_id)),
            )
            .join(
                Employee,
                and_(
                    Employee.id == EmployeeProperty.employee_id,
                    Employee.tenant_id == EmployeeProperty.tenant_id,
                ),
            )
            .where(
                EmployeeProperty.tenant_id == tenant_id,
                EmployeeProperty.property_id.in_(scoped_ids),
                EmployeeProperty.status == "ACTIVE",
                EmployeeProperty.valid_from <= at,
                or_(
                    EmployeeProperty.valid_to.is_(None),
                    EmployeeProperty.valid_to > at,
                ),
                Employee.status == "ACTIVE",
            )
            .group_by(EmployeeProperty.property_id)
            .order_by(EmployeeProperty.property_id)
        ).all()
        return tuple((int(row[0]), int(row[1])) for row in rows)

    def list_current_staffing(
        self,
        *,
        tenant_id: int,
        business_dates: Mapping[int, date],
        at: datetime,
    ) -> tuple[StaffingRow, ...]:
        if not business_dates:
            return ()
        date_filters = tuple(
            and_(
                EmployeeDailyStatus.property_id == property_id,
                EmployeeDailyStatus.business_date == business_date,
            )
            for property_id, business_date in sorted(business_dates.items())
        )
        on_duty = EmployeeDailyStatus.on_duty == "ON_DUTY"
        rows = self._session.execute(
            select(
                EmployeeDailyStatus.property_id,
                func.count(EmployeeDailyStatus.id),
                func.sum(case((on_duty, 1), else_=0)),
                func.sum(
                    case(
                        (
                            and_(
                                on_duty,
                                EmployeeDailyStatus.current_load < Employee.max_capacity,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case((on_duty, EmployeeDailyStatus.current_load), else_=0)
                ),
                func.sum(case((on_duty, Employee.max_capacity), else_=0)),
            )
            .join(
                Employee,
                and_(
                    Employee.id == EmployeeDailyStatus.employee_id,
                    Employee.tenant_id == EmployeeDailyStatus.tenant_id,
                ),
            )
            .join(
                EmployeeProperty,
                and_(
                    EmployeeProperty.employee_id == EmployeeDailyStatus.employee_id,
                    EmployeeProperty.tenant_id == EmployeeDailyStatus.tenant_id,
                    EmployeeProperty.property_id == EmployeeDailyStatus.property_id,
                ),
            )
            .where(
                EmployeeDailyStatus.tenant_id == tenant_id,
                Employee.status == "ACTIVE",
                EmployeeProperty.status == "ACTIVE",
                EmployeeProperty.valid_from <= at,
                or_(
                    EmployeeProperty.valid_to.is_(None),
                    EmployeeProperty.valid_to > at,
                ),
                or_(*date_filters),
            )
            .group_by(EmployeeDailyStatus.property_id)
            .order_by(EmployeeDailyStatus.property_id)
        ).all()
        return tuple(
            (
                int(row[0]),
                int(row[1]),
                int(row[2] or 0),
                int(row[3] or 0),
                int(row[4] or 0),
                int(row[5] or 0),
            )
            for row in rows
        )
