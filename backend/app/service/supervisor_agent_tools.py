"""Read tools with request-fixed scope and a fresh session for each invocation."""
from contextlib import contextmanager

from app.core.database import get_session_factory
from app.core.logging import get_logger
from app.provider.langchain_llm import LangChainUnavailable
from app.provider.supervisor_agent import LangChainSupervisorPlanner, SupervisorPlanner
from app.service.supervisor_agent import SupervisorAgentService
from app.service.work_order import WorkOrderService

logger = get_logger(__name__)


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


def build_supervisor_planner(settings):
    """PA-165: pick the planning engine, falling back to `direct` with a logged reason.

    `SUPERVISOR_AGENT_ENGINE` defaults to `direct`, so behaviour is unchanged unless someone
    opts in. When `langchain` is requested but the optional extra is missing, the agent keeps
    working on the reviewed direct planner and the reason lands in the log instead of being
    swallowed.
    """

    engine = getattr(settings, 'supervisor_agent_engine', 'direct')
    if engine != 'langchain':
        return SupervisorPlanner(settings)
    try:
        planner = LangChainSupervisorPlanner(settings)
    except LangChainUnavailable as exc:
        logger.error('supervisor_agent_engine_unavailable', engine='langchain',
                     reason=str(exc), fallback_engine='direct')
        return SupervisorPlanner(settings)
    logger.info('supervisor_agent_engine_selected', engine='langchain', mode=planner.mode)
    return planner


def build_supervisor_agent(*, actor, property_id, settings):
    tools = SupervisorReadTools(session_factory=get_session_factory(settings.active_database_url),
                                settings=settings, actor=actor, property_id=property_id)
    return SupervisorAgentService(tools=tools, planner=build_supervisor_planner(settings))
