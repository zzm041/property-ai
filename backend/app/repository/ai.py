"""Persistence helpers for AI analysis, incidents, and announcements."""

from datetime import datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.model.ai import (
    AiCallLog,
    Announcement,
    AnnouncementReview,
    AnnouncementScope,
    DispatchRecommendation,
    Incident,
    IncidentWorkOrder,
    Notification,
    WorkOrderAnalysis,
)
from app.model.employee import (
    AttendanceRecord,
    Duty,
    Employee,
    EmployeeDailyStatus,
    EmployeeDuty,
    EmployeeProperty,
    EmployeeSkill,
    Skill,
)
from app.model.identity import Role, User, UserRole
from app.model.property import House, Unit
from app.model.work_order import (
    FileObject,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderFeedback,
    WorkOrderMedia,
    WorkOrderProcess,
    WorkOrderReceipt,
)


class AiRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_analysis(self, analysis_id: int) -> WorkOrderAnalysis | None:
        return self._session.get(WorkOrderAnalysis, analysis_id)

    def get_current_analysis(self, work_order_id: int) -> WorkOrderAnalysis | None:
        return self._session.scalar(
            select(WorkOrderAnalysis)
            .where(WorkOrderAnalysis.work_order_id == work_order_id)
            .order_by(WorkOrderAnalysis.analysis_version.desc())
            .limit(1)
        )

    def get_current_analysis_for_update(
        self,
        work_order_id: int,
    ) -> WorkOrderAnalysis | None:
        return self._session.scalar(
            select(WorkOrderAnalysis)
            .where(WorkOrderAnalysis.work_order_id == work_order_id)
            .order_by(WorkOrderAnalysis.analysis_version.desc())
            .limit(1)
            .with_for_update()
        )

    def add_analysis(self, record: WorkOrderAnalysis) -> WorkOrderAnalysis:
        self._session.add(record)
        self._session.flush()
        return record

    def add_call_log(self, record: AiCallLog) -> AiCallLog:
        self._session.add(record)
        self._session.flush()
        return record

    def delete_recommendations(self, analysis_id: int) -> None:
        existing = self._session.scalars(
            select(DispatchRecommendation).where(
                DispatchRecommendation.analysis_id == analysis_id
            )
        ).all()
        for item in existing:
            self._session.delete(item)
        self._session.flush()

    def add_recommendation(
        self,
        record: DispatchRecommendation,
    ) -> DispatchRecommendation:
        self._session.add(record)
        self._session.flush()
        return record

    def list_recommendations(
        self,
        work_order_id: int,
    ) -> tuple[DispatchRecommendation, ...]:
        return tuple(
            self._session.scalars(
                select(DispatchRecommendation)
                .where(DispatchRecommendation.work_order_id == work_order_id)
                .order_by(
                    DispatchRecommendation.eligible.desc(),
                    DispatchRecommendation.rank_no,
                    DispatchRecommendation.id,
                )
            ).all()
        )

    def get_file(self, file_id: int) -> FileObject | None:
        return self._session.get(FileObject, file_id)

    def get_building_id(self, house_id: int) -> int | None:
        house = self._session.get(House, house_id)
        if house is None:
            return None
        unit = self._session.get(Unit, house.unit_id)
        return None if unit is None else unit.building_id

    def list_property_employees(self, *, tenant_id: int, property_id: int) -> tuple[Employee, ...]:
        return tuple(
            self._session.scalars(
                select(Employee)
                .join(
                    EmployeeProperty,
                    EmployeeProperty.employee_id == Employee.id,
                )
                .where(
                    Employee.tenant_id == tenant_id,
                    EmployeeProperty.tenant_id == tenant_id,
                    EmployeeProperty.property_id == property_id,
                )
                .distinct()
                .order_by(Employee.id)
            ).all()
        )

    def list_employee_duties(self, employee_id: int) -> frozenset[str]:
        rows = self._session.execute(
            select(Duty.code)
            .join(EmployeeDuty, EmployeeDuty.duty_id == Duty.id)
            .where(
                EmployeeDuty.employee_id == employee_id,
                Duty.status == "ACTIVE",
            )
        ).all()
        return frozenset(row[0] for row in rows)

    def list_employee_skills(self, employee_id: int) -> frozenset[str]:
        rows = self._session.execute(
            select(Skill.code)
            .join(EmployeeSkill, EmployeeSkill.skill_id == Skill.id)
            .where(
                EmployeeSkill.employee_id == employee_id,
                Skill.status == "ACTIVE",
            )
        ).all()
        return frozenset(row[0] for row in rows)

    def get_user_status(self, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        return self._session.scalar(select(User.status).where(User.id == user_id))

    def list_employee_role_codes(
        self,
        user_id: int,
        *,
        tenant_id: int,
        property_id: int,
    ) -> frozenset[str]:
        rows = self._session.execute(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                (
                    (UserRole.scope_type == "SYSTEM")
                    | (
                        (UserRole.scope_type == "TENANT")
                        & (UserRole.tenant_id == tenant_id)
                    )
                    | (
                        (UserRole.scope_type == "PROPERTY")
                        & (UserRole.tenant_id == tenant_id)
                        & (UserRole.property_id == property_id)
                    )
                ),
            )
        ).all()
        return frozenset(row[0] for row in rows)

    def get_effective_property(
        self,
        *,
        employee_id: int,
        property_id: int,
        now: datetime,
    ) -> EmployeeProperty | None:
        return self._session.scalar(
            select(EmployeeProperty).where(
                EmployeeProperty.employee_id == employee_id,
                EmployeeProperty.property_id == property_id,
                EmployeeProperty.status == "ACTIVE",
                EmployeeProperty.valid_from <= now,
                (EmployeeProperty.valid_to.is_(None))
                | (EmployeeProperty.valid_to > now),
            )
        )

    def get_daily_status(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date,
    ) -> EmployeeDailyStatus | None:
        return self._session.scalar(
            select(EmployeeDailyStatus).where(
                EmployeeDailyStatus.employee_id == employee_id,
                EmployeeDailyStatus.property_id == property_id,
                EmployeeDailyStatus.business_date == business_date,
            )
        )

    def get_attendance(
        self,
        *,
        employee_id: int,
        property_id: int,
        business_date,
    ) -> AttendanceRecord | None:
        return self._session.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.property_id == property_id,
                AttendanceRecord.business_date == business_date,
                AttendanceRecord.check_out_at.is_(None),
            )
        )

    def get_dispatch_history(
        self,
        *,
        employee_id: int,
        tenant_id: int,
        property_id: int,
    ) -> tuple[float | None, float | None, float | None]:
        assignment_rows = self._session.execute(
            select(
                WorkOrderAssignment.assigned_at,
                WorkOrderAssignment.accept_at,
            )
            .where(
                WorkOrderAssignment.employee_id == employee_id,
                WorkOrderAssignment.tenant_id == tenant_id,
                WorkOrderAssignment.property_id == property_id,
            )
        ).all()
        response_seconds = [
            max((accepted_at - assigned_at).total_seconds(), 0.0)
            for assigned_at, accepted_at in assignment_rows
            if accepted_at is not None
        ]
        average_response = (
            sum(response_seconds) / len(response_seconds)
            if response_seconds
            else None
        )
        process_rows = self._session.execute(
            select(WorkOrderProcess.id, WorkOrderReceipt.resolved)
            .outerjoin(
                WorkOrderReceipt,
                WorkOrderReceipt.process_id == WorkOrderProcess.id,
            )
            .where(
                WorkOrderProcess.assignee_id == employee_id,
                WorkOrderProcess.tenant_id == tenant_id,
                WorkOrderProcess.property_id == property_id,
            )
        ).all()
        completion_rate = (
            sum(1 for _process_id, resolved in process_rows if resolved == 1)
            / len(process_rows)
            if process_rows
            else None
        )
        ratings = tuple(
            float(value)
            for value in self._session.scalars(
                select(WorkOrderFeedback.rating)
                .join(
                    WorkOrderProcess,
                    WorkOrderProcess.id == WorkOrderFeedback.process_id,
                )
                .where(
                    WorkOrderProcess.assignee_id == employee_id,
                    WorkOrderProcess.tenant_id == tenant_id,
                    WorkOrderProcess.property_id == property_id,
                    WorkOrderFeedback.rating.is_not(None),
                )
            ).all()
            if value is not None
        )
        average_rating = sum(ratings) / len(ratings) if ratings else None
        return average_response, completion_rate, average_rating

    def count_same_building_history(
        self,
        *,
        employee_id: int,
        building_id: int | None,
        tenant_id: int,
        property_id: int,
    ) -> int:
        if building_id is None:
            return 0
        rows = self._session.execute(
            select(WorkOrder.id)
            .join(House, House.id == WorkOrder.house_id)
            .join(Unit, Unit.id == House.unit_id)
            .join(
                WorkOrderProcess,
                WorkOrderProcess.work_order_id == WorkOrder.id,
            )
            .join(
                WorkOrderReceipt,
                WorkOrderReceipt.process_id == WorkOrderProcess.id,
            )
            .where(
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.property_id == property_id,
                Unit.building_id == building_id,
                WorkOrderProcess.assignee_id == employee_id,
                WorkOrderReceipt.resolved == 1,
            )
            .distinct()
        ).all()
        return len(rows)

    def list_cluster_work_orders(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int | None,
        category: str,
        subcategory: str,
        since: datetime,
        until: datetime,
    ) -> tuple[WorkOrder, ...]:
        query = select(WorkOrder).where(
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.property_id == property_id,
            WorkOrder.category == category,
            WorkOrder.created_at >= since,
            WorkOrder.created_at <= until,
            WorkOrder.status.notin_(("CANCELLED", "CLOSED")),
        )
        records = tuple(self._session.scalars(query.order_by(WorkOrder.id)).all())
        matched: list[WorkOrder] = []
        for record in records:
            if (
                building_id is not None
                and self.get_building_id(record.house_id) != building_id
            ):
                continue
            analysis = self.get_current_analysis(record.id)
            if (
                analysis is None
                or analysis.status != "COMPLETED"
                or analysis.subcategory != subcategory
            ):
                continue
            matched.append(record)
        return tuple(matched)

    def find_open_incident(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_id: int | None,
        category: str,
        subcategory: str,
        seen_after: datetime,
        seen_before: datetime,
    ) -> Incident | None:
        return self._session.scalar(
            select(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.property_id == property_id,
                Incident.building_id == building_id,
                Incident.category == category,
                Incident.subcategory == subcategory,
                Incident.status.in_(("OPEN", "MONITORING")),
                Incident.last_seen_at >= seen_after,
                Incident.first_seen_at <= seen_before,
            )
            .order_by(Incident.id)
            .limit(1)
            .with_for_update()
        )

    def get_incident(self, incident_id: int) -> Incident | None:
        return self._session.get(Incident, incident_id)

    def get_incident_for_update(self, incident_id: int) -> Incident | None:
        return self._session.scalar(
            select(Incident)
            .where(Incident.id == incident_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def list_active_incident_assignments_for_update(
        self,
        *,
        incident_id: int,
        exclude_assignment_id: int,
    ) -> tuple[tuple[WorkOrder, WorkOrderAssignment], ...]:
        return tuple(
            self._session.execute(
                select(WorkOrder, WorkOrderAssignment)
                .join(
                    IncidentWorkOrder,
                    IncidentWorkOrder.work_order_id == WorkOrder.id,
                )
                .join(
                    WorkOrderAssignment,
                    WorkOrderAssignment.id == WorkOrder.current_assignment_id,
                )
                .where(
                    IncidentWorkOrder.incident_id == incident_id,
                    WorkOrderAssignment.id != exclude_assignment_id,
                    WorkOrderAssignment.status == "ACTIVE",
                    WorkOrderAssignment.ended_at.is_(None),
                )
                .order_by(WorkOrder.created_at, WorkOrder.id)
                .with_for_update()
            ).all()
        )

    def add_incident(self, record: Incident) -> Incident:
        self._session.add(record)
        self._session.flush()
        return record

    def get_incident_link(self, work_order_id: int) -> IncidentWorkOrder | None:
        return self._session.scalar(
            select(IncidentWorkOrder).where(
                IncidentWorkOrder.work_order_id == work_order_id
            )
        )

    def add_incident_link(self, record: IncidentWorkOrder) -> IncidentWorkOrder:
        self._session.add(record)
        self._session.flush()
        return record

    def list_incident_work_orders(self, incident_id: int) -> tuple[IncidentWorkOrder, ...]:
        return tuple(
            self._session.scalars(
                select(IncidentWorkOrder)
                .where(IncidentWorkOrder.incident_id == incident_id)
                .order_by(IncidentWorkOrder.id)
            ).all()
        )

    def list_incidents(
        self,
        *,
        tenant_id: int,
        property_id: int,
        status: str | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Incident, ...]:
        query = select(Incident).where(
            Incident.tenant_id == tenant_id,
            Incident.property_id == property_id,
            Incident.id > after_id,
        )
        if status is not None:
            query = query.where(Incident.status == status)
        return tuple(
            self._session.scalars(
                query.order_by(Incident.id).limit(limit)
            ).all()
        )

    def list_incidents_for_participant(
        self,
        *,
        tenant_id: int,
        property_id: int,
        employee_id: int,
        status: str | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Incident, ...]:
        query = (
            select(Incident)
            .join(
                IncidentWorkOrder,
                IncidentWorkOrder.incident_id == Incident.id,
            )
            .join(
                WorkOrder,
                WorkOrder.id == IncidentWorkOrder.work_order_id,
            )
            .join(
                WorkOrderAssignment,
                WorkOrderAssignment.id == WorkOrder.current_assignment_id,
            )
            .where(
                Incident.tenant_id == tenant_id,
                Incident.property_id == property_id,
                Incident.id > after_id,
                IncidentWorkOrder.tenant_id == tenant_id,
                IncidentWorkOrder.property_id == property_id,
                WorkOrder.tenant_id == tenant_id,
                WorkOrder.property_id == property_id,
                WorkOrderAssignment.tenant_id == tenant_id,
                WorkOrderAssignment.property_id == property_id,
                WorkOrderAssignment.employee_id == employee_id,
                WorkOrderAssignment.status == "ACTIVE",
                WorkOrderAssignment.ended_at.is_(None),
            )
            .distinct()
        )
        if status is not None:
            query = query.where(Incident.status == status)
        return tuple(
            self._session.scalars(
                query.order_by(Incident.id).limit(limit)
            ).all()
        )

    def is_incident_participant(
        self,
        *,
        incident_id: int,
        tenant_id: int,
        property_id: int,
        employee_id: int,
    ) -> bool:
        return (
            self._session.scalar(
                select(IncidentWorkOrder.id)
                .join(
                    WorkOrder,
                    WorkOrder.id == IncidentWorkOrder.work_order_id,
                )
                .join(
                    WorkOrderAssignment,
                    WorkOrderAssignment.id == WorkOrder.current_assignment_id,
                )
                .where(
                    IncidentWorkOrder.incident_id == incident_id,
                    IncidentWorkOrder.tenant_id == tenant_id,
                    IncidentWorkOrder.property_id == property_id,
                    WorkOrder.tenant_id == tenant_id,
                    WorkOrder.property_id == property_id,
                    WorkOrderAssignment.tenant_id == tenant_id,
                    WorkOrderAssignment.property_id == property_id,
                    WorkOrderAssignment.employee_id == employee_id,
                    WorkOrderAssignment.status == "ACTIVE",
                    WorkOrderAssignment.ended_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    def add_announcement(self, record: Announcement) -> Announcement:
        self._session.add(record)
        self._session.flush()
        return record

    def get_announcement(self, announcement_id: int) -> Announcement | None:
        return self._session.get(Announcement, announcement_id)

    def get_announcement_for_update(
        self,
        announcement_id: int,
    ) -> Announcement | None:
        return self._session.scalar(
            select(Announcement)
            .where(Announcement.id == announcement_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def add_announcement_scope(self, record: AnnouncementScope) -> AnnouncementScope:
        self._session.add(record)
        self._session.flush()
        return record

    def list_announcement_scopes(
        self,
        announcement_id: int,
    ) -> tuple[AnnouncementScope, ...]:
        return tuple(
            self._session.scalars(
                select(AnnouncementScope)
                .where(AnnouncementScope.announcement_id == announcement_id)
                .order_by(AnnouncementScope.id)
            ).all()
        )

    def replace_announcement_scope(
        self,
        *,
        announcement_id: int,
        scope: AnnouncementScope,
    ) -> AnnouncementScope:
        self._session.execute(
            delete(AnnouncementScope).where(
                AnnouncementScope.announcement_id == announcement_id
            )
        )
        self._session.add(scope)
        self._session.flush()
        return scope

    def add_announcement_review(
        self,
        record: AnnouncementReview,
    ) -> AnnouncementReview:
        self._session.add(record)
        self._session.flush()
        return record

    def list_announcement_reviews(
        self,
        announcement_id: int,
    ) -> tuple[AnnouncementReview, ...]:
        return tuple(
            self._session.scalars(
                select(AnnouncementReview)
                .where(AnnouncementReview.announcement_id == announcement_id)
                .order_by(AnnouncementReview.id)
            ).all()
        )

    def list_announcements_for_management(
        self,
        *,
        tenant_id: int,
        property_id: int,
        include_tenant_targets: bool,
        include_property_targets: bool,
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Announcement, ...]:
        target_conditions = []
        if include_tenant_targets:
            target_conditions.append(AnnouncementScope.scope_type == "TENANT")
        if include_property_targets:
            target_conditions.append(
                and_(
                    Announcement.property_id == property_id,
                    AnnouncementScope.scope_type != "TENANT",
                )
            )
        if not target_conditions:
            return ()
        return tuple(
            self._session.scalars(
                select(Announcement)
                .join(
                    AnnouncementScope,
                    AnnouncementScope.announcement_id == Announcement.id,
                )
                .where(
                    Announcement.tenant_id == tenant_id,
                    Announcement.id > after_id,
                    or_(*target_conditions),
                )
                .distinct()
                .order_by(Announcement.id)
                .limit(limit)
            ).all()
        )

    def list_published_announcements(
        self,
        *,
        tenant_id: int,
        property_id: int,
        building_ids: tuple[int, ...],
        unit_ids: tuple[int, ...],
        audiences: tuple[str, ...],
        after_id: int = 0,
        limit: int = 50,
    ) -> tuple[Announcement, ...]:
        audience_condition = AnnouncementScope.audience.in_(audiences)
        target_conditions = [
            and_(
                AnnouncementScope.scope_type == "TENANT",
                audience_condition,
            ),
            and_(
                Announcement.property_id == property_id,
                AnnouncementScope.scope_type.in_(("PROPERTY", "AUDIENCE")),
                audience_condition,
            ),
        ]
        if "STAFF" in audiences:
            target_conditions.append(
                and_(
                    Announcement.property_id == property_id,
                    AnnouncementScope.scope_type.in_(("BUILDING", "UNIT")),
                    AnnouncementScope.audience == "STAFF",
                )
            )
        if building_ids:
            target_conditions.append(
                and_(
                    Announcement.property_id == property_id,
                    AnnouncementScope.scope_type == "BUILDING",
                    AnnouncementScope.building_id.in_(building_ids),
                    audience_condition,
                )
            )
        if unit_ids:
            target_conditions.append(
                and_(
                    Announcement.property_id == property_id,
                    AnnouncementScope.scope_type == "UNIT",
                    AnnouncementScope.unit_id.in_(unit_ids),
                    audience_condition,
                )
            )
        return tuple(
            self._session.scalars(
                select(Announcement)
                .join(
                    AnnouncementScope,
                    AnnouncementScope.announcement_id == Announcement.id,
                )
                .where(
                    Announcement.tenant_id == tenant_id,
                    Announcement.status == "PUBLISHED",
                    Announcement.id > after_id,
                    or_(*target_conditions),
                )
                .distinct()
                .order_by(Announcement.id)
                .limit(limit)
            ).all()
        )

    def add_notification(self, record: Notification) -> Notification:
        self._session.add(record)
        self._session.flush()
        return record

    def list_media_with_files(
        self,
        work_order_id: int,
    ) -> tuple[tuple[WorkOrderMedia, FileObject], ...]:
        rows = self._session.execute(
            select(WorkOrderMedia, FileObject)
            .join(FileObject, FileObject.id == WorkOrderMedia.file_id)
            .where(WorkOrderMedia.work_order_id == work_order_id)
            .order_by(WorkOrderMedia.id)
        ).all()
        return tuple((media, file_object) for media, file_object in rows)
