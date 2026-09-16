"""Query/add/flush for WorkOrder aggregates. Service owns commits."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model.property import House, Property
from app.model.work_order import (
    FileObject,
    IdempotencyRecord,
    SlaClock,
    SlaPolicy,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderContent,
    WorkOrderFeedback,
    WorkOrderLog,
    WorkOrderMedia,
    WorkOrderProcess,
    WorkOrderReceipt,
)


class WorkOrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_work_order(self, work_order_id: int) -> WorkOrder | None:
        return self._session.get(WorkOrder, work_order_id)

    def get_work_order_for_update(self, work_order_id: int) -> WorkOrder | None:
        return self._session.scalar(
            select(WorkOrder)
            .where(WorkOrder.id == work_order_id)
            .with_for_update()
        )

    def get_house(self, house_id: int) -> House | None:
        return self._session.get(House, house_id)

    def get_property(self, property_id: int) -> Property | None:
        return self._session.get(Property, property_id)

    def get_file(self, file_id: int) -> FileObject | None:
        return self._session.get(FileObject, file_id)

    def get_idempotency(
        self,
        *,
        user_id: int,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        return self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    def find_sla_policy(
        self,
        *,
        tenant_id: int,
        property_id: int,
        category: str,
        priority: str,
    ) -> SlaPolicy | None:
        property_level = self._session.scalar(
            select(SlaPolicy).where(
                SlaPolicy.tenant_id == tenant_id,
                SlaPolicy.property_id == property_id,
                SlaPolicy.category == category,
                SlaPolicy.priority == priority,
                SlaPolicy.status == "ACTIVE",
            )
        )
        if property_level is not None:
            return property_level
        return self._session.scalar(
            select(SlaPolicy).where(
                SlaPolicy.tenant_id == tenant_id,
                SlaPolicy.property_id.is_(None),
                SlaPolicy.category == category,
                SlaPolicy.priority == priority,
                SlaPolicy.status == "ACTIVE",
            )
        )

    def get_active_assignment(
        self,
        work_order_id: int,
    ) -> WorkOrderAssignment | None:
        return self._session.scalar(
            select(WorkOrderAssignment).where(
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
            )
        )

    def get_active_assignment_for_update(
        self,
        work_order_id: int,
    ) -> WorkOrderAssignment | None:
        return self._session.scalar(
            select(WorkOrderAssignment)
            .where(
                WorkOrderAssignment.work_order_id == work_order_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
            )
            .with_for_update()
        )

    def list_assignments(self, work_order_id: int) -> tuple[WorkOrderAssignment, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderAssignment)
                .where(WorkOrderAssignment.work_order_id == work_order_id)
                .order_by(WorkOrderAssignment.id)
            ).all()
        )

    def get_content(self, work_order_id: int) -> WorkOrderContent | None:
        return self._session.scalar(
            select(WorkOrderContent).where(
                WorkOrderContent.work_order_id == work_order_id
            )
        )

    def list_media(self, work_order_id: int) -> tuple[WorkOrderMedia, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderMedia)
                .where(WorkOrderMedia.work_order_id == work_order_id)
                .order_by(WorkOrderMedia.id)
            ).all()
        )

    def list_logs(self, work_order_id: int) -> tuple[WorkOrderLog, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderLog)
                .where(WorkOrderLog.work_order_id == work_order_id)
                .order_by(WorkOrderLog.id)
            ).all()
        )

    def list_processes(self, work_order_id: int) -> tuple[WorkOrderProcess, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderProcess)
                .where(WorkOrderProcess.work_order_id == work_order_id)
                .order_by(WorkOrderProcess.cycle_no)
            ).all()
        )

    def get_current_process(
        self,
        work_order_id: int,
    ) -> WorkOrderProcess | None:
        return self._session.scalar(
            select(WorkOrderProcess)
            .where(WorkOrderProcess.work_order_id == work_order_id)
            .order_by(WorkOrderProcess.cycle_no.desc())
            .limit(1)
        )

    def get_receipt_for_process(self, process_id: int) -> WorkOrderReceipt | None:
        return self._session.scalar(
            select(WorkOrderReceipt).where(WorkOrderReceipt.process_id == process_id)
        )

    def list_feedbacks(self, work_order_id: int) -> tuple[WorkOrderFeedback, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrderFeedback)
                .where(WorkOrderFeedback.work_order_id == work_order_id)
                .order_by(WorkOrderFeedback.id)
            ).all()
        )

    def get_sla_clock(self, work_order_id: int) -> SlaClock | None:
        return self._session.scalar(
            select(SlaClock).where(SlaClock.work_order_id == work_order_id)
        )

    def list_queue_for_property(
        self,
        *,
        tenant_id: int,
        property_id: int,
        statuses: tuple[str, ...],
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .where(
                    WorkOrder.tenant_id == tenant_id,
                    WorkOrder.property_id == property_id,
                    WorkOrder.status.in_(statuses),
                    WorkOrder.id > after_id,
                )
                .order_by(WorkOrder.id)
                .limit(limit)
            ).all()
        )

    def list_for_assignee(
        self,
        *,
        employee_id: int,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[WorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(WorkOrder)
                .join(
                    WorkOrderAssignment,
                    (WorkOrderAssignment.work_order_id == WorkOrder.id)
                    & (WorkOrderAssignment.status == "ACTIVE")
                    & WorkOrderAssignment.ended_at.is_(None),
                )
                .where(
                    WorkOrderAssignment.employee_id == employee_id,
                    WorkOrder.id > after_id,
                )
                .order_by(WorkOrder.id)
                .limit(limit)
            ).all()
        )

    def add_file(self, record: FileObject) -> FileObject:
        self._session.add(record)
        self._session.flush()
        return record

    def add_work_order(self, record: WorkOrder) -> WorkOrder:
        self._session.add(record)
        self._session.flush()
        return record

    def add_content(self, record: WorkOrderContent) -> WorkOrderContent:
        self._session.add(record)
        self._session.flush()
        return record

    def add_media(self, record: WorkOrderMedia) -> WorkOrderMedia:
        self._session.add(record)
        self._session.flush()
        return record

    def add_assignment(self, record: WorkOrderAssignment) -> WorkOrderAssignment:
        self._session.add(record)
        self._session.flush()
        return record

    def add_log(self, record: WorkOrderLog) -> WorkOrderLog:
        self._session.add(record)
        self._session.flush()
        return record

    def add_process(self, record: WorkOrderProcess) -> WorkOrderProcess:
        self._session.add(record)
        self._session.flush()
        return record

    def add_receipt(self, record: WorkOrderReceipt) -> WorkOrderReceipt:
        self._session.add(record)
        self._session.flush()
        return record

    def add_feedback(self, record: WorkOrderFeedback) -> WorkOrderFeedback:
        self._session.add(record)
        self._session.flush()
        return record

    def add_sla_clock(self, record: SlaClock) -> SlaClock:
        self._session.add(record)
        self._session.flush()
        return record

    def add_idempotency(self, record: IdempotencyRecord) -> IdempotencyRecord:
        self._session.add(record)
        self._session.flush()
        return record

    def add_sla_policy(self, record: SlaPolicy) -> SlaPolicy:
        self._session.add(record)
        self._session.flush()
        return record
