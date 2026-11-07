"""Read tools with request-fixed scope and a fresh session for each invocation."""
from contextlib import contextmanager

from app.core.database import get_session_factory
from app.provider.supervisor_agent import SupervisorPlanner
from app.service.supervisor_agent import SupervisorAgentService
from app.service.work_order import WorkOrderService


class SupervisorReadTools:
    def __init__(self, *, session_factory, settings, actor, property_id):
        self.session_factory = session_factory
        self.settings = settings
        self.actor = actor
        self.property_id = property_id

    @contextmanager
    def _service(self):
        session = self.session_factory()
        try:
            yield WorkOrderService(session, settings=self.settings)
        finally:
            session.close()

    def authorize(self):
        with self._service() as service:
            service.list_supervisor_queue(actor=self.actor, property_id=self.property_id, limit=1)

    def queue(self):
        with self._service() as service:
            records = service.list_supervisor_queue(actor=self.actor, property_id=self.property_id, limit=5)
            return [{'id': row.id, 'version': row.version, 'status': row.status,
                     'priority': row.priority, 'process_reason': row.process_reason,
                     'sla_overdue': service.is_overdue(row)} for row in records]

    def diagnose(self, work_order_id, version):
        with self._service() as service:
            data = service.diagnose_work_order(actor=self.actor, property_id=self.property_id,
                work_order_id=work_order_id, version=version).model_dump(mode='json')
            return {key: data[key] for key in (
                'work_order_id', 'property_id', 'version', 'facts', 'suggested_actions')}


def build_supervisor_agent(*, actor, property_id, settings):
    tools = SupervisorReadTools(session_factory=get_session_factory(settings.active_database_url),
                                settings=settings, actor=actor, property_id=property_id)
    return SupervisorAgentService(tools=tools, planner=SupervisorPlanner(settings))
