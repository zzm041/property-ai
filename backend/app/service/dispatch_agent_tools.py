"""DB-backed tools for the dispatch agent loop: three reads and one suggestion.

`propose_assignment` is deliberately a suggestion builder: it validates eligibility
and returns the candidate a supervisor should confirm. It never writes an
assignment, never moves a work order and never touches the audit trail - the loop
writes the audit rows itself.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from app.domain.ai import category_duty
from app.domain.work_order import EmployeeStatus, WorkOrderCategory
from app.repository.ai import AiRepository
from app.service.work_order import WorkOrderService

ACTIVE = "ACTIVE"
UNCONSTRAINED_SKILLS = frozenset({"OTHER", ""})
ON_DUTY = "ON_DUTY"


def build_dispatch_agent(*, actor, property_id: int, settings, planner=None):
    """Wire the loop for one supervisor request: real DB tools, real audit rows."""

    from app.core.database import get_session_factory
    from app.provider.llm.dispatch_planner import DispatchPlanner
    from app.service.dispatch_agent import AgentStepRecorder, DispatchAgentLoop

    factory = get_session_factory(settings.active_database_url)
    tools = DispatchAgentTools(session_factory=factory, settings=settings,
                               actor=actor, property_id=property_id)
    recorder = AgentStepRecorder(session_factory=factory, settings=settings)
    return DispatchAgentLoop(tools=tools, planner=planner or DispatchPlanner(settings),
                             recorder=recorder)


class DispatchAgentTools:
    """Four fixed tools over a request-fixed property scope."""

    def __init__(self, *, session_factory, settings, actor, property_id: int):
        self.session_factory = session_factory
        self.settings = settings
        self.actor = actor
        self.property_id = property_id

    # ------------------------------------------------------------- plumbing
    @contextmanager
    def _session(self):
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def _service(self, session):
        return WorkOrderService(session, settings=self.settings)

    # ----------------------------------------------------------------- tools
    def list_pending_work_orders(self, limit: int = 5) -> list[dict]:
        limit = max(1, min(int(limit), 5))
        with self._session() as session:
            service = self._service(session)
            records = service.list_supervisor_queue(
                actor=self.actor, property_id=self.property_id, limit=limit, view="dispatch")
            return [
                {
                    "work_order_id": row.id,
                    "version": row.version,
                    "status": row.status,
                    "priority": row.priority,
                    "category": row.category,
                    "sla_overdue": service.is_overdue(row),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in records
            ]

    def get_work_order(self, work_order_id: int) -> dict:
        with self._session() as session:
            service = self._service(session)
            record = service.get_work_order(actor=self.actor, work_order_id=int(work_order_id))
            analysis = AiRepository(session).get_current_analysis(record.id)
            return {
                "work_order_id": record.id,
                "version": record.version,
                "status": record.status,
                "priority": record.priority,
                "category": record.category,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "has_analysis": analysis is not None,
                "required_duty": _duty_for(analysis),
                "required_skill": None if analysis is None else analysis.required_skill,
                "analysis_status": None if analysis is None else analysis.status,
            }

    def list_eligible_staff(self, work_order_id: int, business_date: date | None = None) -> dict:
        with self._session() as session:
            record, required_duty, required_skill = self._target(
                session, work_order_id, business_date)
            eligible, blocked = self._evaluate(
                session, record, required_duty, required_skill, business_date)
            return {
                "work_order_id": record.id,
                "required_duty": required_duty,
                "required_skill": required_skill,
                "eligible_count": len(eligible),
                "eligible": eligible,
                "blocked": blocked,
            }

    def propose_assignment(self, work_order_id: int, employee_id: int | None = None,
                           reason: str = "", business_date: date | None = None) -> dict:
        with self._session() as session:
            record, required_duty, required_skill = self._target(
                session, work_order_id, business_date)
            eligible, blocked = self._evaluate(
                session, record, required_duty, required_skill, business_date)
            chosen = None
            if employee_id is not None:
                chosen = next((row for row in eligible if row["employee_id"] == int(employee_id)), None)
            elif eligible:
                chosen = eligible[0]
            suggestion = None
            if chosen is not None:
                suggestion = {
                    "work_order_id": record.id,
                    "version": record.version,
                    "employee_id": chosen["employee_id"],
                    "on_duty": chosen["on_duty"],
                    "remaining_capacity": chosen["remaining_capacity"],
                    "reason": (reason or "Top eligible candidate for this work order")[:200],
                    "requires_supervisor_confirmation": True,
                    "applied": False,
                }
            return {
                "work_order_id": record.id,
                "employee_id": None if chosen is None else chosen["employee_id"],
                "applied": False,
                "eligible_count": len(eligible),
                "eligible": eligible,
                "blocked": blocked,
                "suggestion": suggestion,
            }

    # --------------------------------------------------------------- helpers
    def _target(self, session, work_order_id: int, business_date: date | None):
        service = self._service(session)
        record = service.get_work_order(actor=self.actor, work_order_id=int(work_order_id))
        analysis = AiRepository(session).get_current_analysis(record.id)
        if analysis is None:
            return record, None, None
        return record, _duty_for(analysis), analysis.required_skill

    def _evaluate(self, session, record, required_duty, required_skill,
                  business_date: date | None) -> tuple[list[dict], dict[str, int]]:
        ai = AiRepository(session)
        employees = ai.list_property_employees(
            tenant_id=record.tenant_id, property_id=record.property_id)
        wants_skill = required_skill not in (None,) and required_skill not in UNCONSTRAINED_SKILLS
        eligible: list[dict] = []
        blocked: dict[str, int] = {}
        for employee in employees:
            def _block(reason: str) -> None:
                blocked[reason] = blocked.get(reason, 0) + 1

            if employee.status != EmployeeStatus.ACTIVE.value:
                _block("EMPLOYEE_INACTIVE")
                continue
            if ai.get_user_status(employee.user_id) != ACTIVE:
                _block("USER_INACTIVE")
                continue
            skills = ai.list_employee_skills(employee.id)
            if wants_skill and required_skill not in skills:
                _block("SKILL_MISMATCH")
                continue
            duties = ai.list_employee_duties(employee.id)
            if required_duty and required_duty not in duties:
                _block("DUTY_MISMATCH")
                continue
            daily = ai.get_daily_status(
                employee_id=employee.id, property_id=record.property_id,
                business_date=business_date) if business_date else None
            on_duty = daily is not None and daily.on_duty == ON_DUTY
            load = 0 if daily is None else int(daily.current_load or 0)
            remaining = int(employee.max_capacity or 0) - load
            if remaining <= 0:
                _block("AT_CAPACITY")
                continue
            if not on_duty:
                _block("OFF_DUTY")
                continue
            eligible.append({
                "employee_id": employee.id,
                "max_capacity": int(employee.max_capacity or 0),
                "current_load": load,
                "remaining_capacity": remaining,
                "on_duty": True,
                "skills": sorted(skills),
            })
        eligible.sort(key=lambda row: (row["remaining_capacity"] * -1, row["employee_id"]))
        return eligible, blocked


def _duty_for(analysis) -> str | None:
    """Map the stored category to the duty a dispatcher must look for."""

    if analysis is None or not getattr(analysis, "category", None):
        return None
    try:
        return category_duty(WorkOrderCategory(analysis.category))
    except ValueError:
        return None
