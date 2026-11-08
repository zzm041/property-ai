"""Real MySQL and HTTP boundaries, with deterministic planning and synthetic data."""
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from app.core.database import get_session_factory
from app.core.errors import AppError
from app.model.ai import WorkOrderAnalysis
from app.model.identity import UserRole
from app.model.work_order import WorkOrder, WorkOrderAssignment, WorkOrderLog
from app.provider.supervisor_agent import SupervisorPlanner
from app.service.supervisor_agent import SupervisorAgentService
from app.service.supervisor_agent_tools import SupervisorReadTools
from tests.integration.conftest import commit_open_transaction
from tests.integration.test_ai_runtime import _auth, _client, _create_work_order, _principal, _settings, _token
from tests.integration.work_order_test_data import create_work_order_graph


@pytest.fixture
def runtime(identity_session, integration_settings):
    graph = create_work_order_graph(identity_session)
    local = _create_work_order(identity_session, user_id=graph.employees.resident_id,
                              house_id=graph.house_1_id, text='synthetic-private-description')
    local_id, version = local.id, local.version
    foreign = _create_work_order(identity_session, user_id=graph.resident_b_id,
                                house_id=graph.house_2_id, text='synthetic-foreign-description')
    foreign_id, foreign_version = foreign.id, foreign.version
    commit_open_transaction(identity_session)
    actor = _principal(identity_session, graph.employees.supervisor_p1_id)
    settings = _settings(supervisor_agent_enabled=True, supervisor_agent_provider='development')
    factory = get_session_factory(integration_settings.test_database_url)
    tools = SupervisorReadTools(session_factory=factory, settings=settings, actor=actor,
                               property_id=graph.employees.property_1_id)
    return SimpleNamespace(graph=graph, local_id=local_id, version=version,
        foreign_id=foreign_id, foreign_version=foreign_version, actor=actor,
        settings=settings, factory=factory, tools=tools, session=identity_session)


def business_snapshot(runtime):
    with runtime.factory() as session:
        return (
            tuple(session.execute(select(WorkOrder.id, WorkOrder.status, WorkOrder.version,
                                         WorkOrder.current_assignment_id).order_by(WorkOrder.id)).all()),
            tuple(session.scalar(select(func.count()).select_from(model))
                  for model in (WorkOrderLog, WorkOrderAssignment, WorkOrderAnalysis)),
        )


class RecordingPlanner:
    mode = 'deterministic_stub'

    def __init__(self, runtime, hook=None):
        self.runtime = runtime
        self.delegate = SupervisorPlanner(runtime.settings)
        self.states = []
        self.hook = hook

    def choose(self, state, *, timeout_seconds):
        # SQL tool reads must release connections before any model wait.
        assert self.runtime.factory.kw['bind'].pool.checkedout() == 0
        self.states.append(state)
        if self.hook:
            self.hook(state)
        return self.delegate.choose(state, timeout_seconds=timeout_seconds)


def test_scoped_real_tools_return_grounded_facts_without_business_writes(runtime):
    before = business_snapshot(runtime)
    planner = RecordingPlanner(runtime)
    service = SupervisorAgentService(tools=runtime.tools, planner=planner)
    result = service.run(question='哪些单需要核实？', request_id='synthetic-agent-read')
    assert result['status'] == 'completed' and result['model_calls'] == 0
    assert [item['work_order_id'] for item in result['items']] == [runtime.local_id]
    queue = planner.states[1]['observations'][0]['result']
    assert [item['id'] for item in queue] == [runtime.local_id]
    assert result['items'][0]['facts'][0]['source_id'] == runtime.local_id
    assert 'synthetic-private-description' not in str(planner.states) + str(result)
    assert '13800000000' not in str(planner.states) + str(result)
    assert business_snapshot(runtime) == before
    assert runtime.factory.kw['bind'].pool.checkedout() == 0


def test_real_tool_rejects_foreign_order_even_with_its_correct_version(runtime):
    with pytest.raises(AppError) as caught:
        runtime.tools.diagnose(runtime.foreign_id, runtime.foreign_version)
    assert caught.value.status_code == 403
    assert runtime.factory.kw['bind'].pool.checkedout() == 0


@pytest.mark.parametrize('change_at', [0, 1, 2])
def test_revocation_committed_during_planning_is_visible_to_next_authorization(runtime, change_at):
    before = business_snapshot(runtime)
    def revoke(state):
        if len(state['observations']) == change_at:
            with runtime.factory.begin() as session:
                session.execute(delete(UserRole).where(UserRole.user_id == runtime.actor.user_id))
    planner = RecordingPlanner(runtime, revoke)
    with pytest.raises(AppError) as caught:
        SupervisorAgentService(tools=runtime.tools, planner=planner).run(
            question='help', request_id='synthetic-agent-revoked')
    assert caught.value.status_code == 403
    assert len(planner.states) == change_at + 1
    assert business_snapshot(runtime) == before
    assert runtime.factory.kw['bind'].pool.checkedout() == 0


@pytest.mark.parametrize('change_at', [1, 2])
def test_other_connection_version_change_blocks_diagnosis_or_final_result(runtime, change_at):
    def change(state):
        if len(state['observations']) == change_at:
            with runtime.factory.begin() as session:
                session.execute(update(WorkOrder).where(WorkOrder.id == runtime.local_id)
                                .values(version=WorkOrder.version + 1))
    planner = RecordingPlanner(runtime, change)
    with pytest.raises(AppError) as caught:
        SupervisorAgentService(tools=runtime.tools, planner=planner).run(
            question='help', request_id='synthetic-agent-version')
    assert caught.value.status_code == 409
    assert runtime.factory.kw['bind'].pool.checkedout() == 0


def test_api_with_real_auth_and_sql_allows_supervisor_but_denies_resident_and_foreign_scope(runtime):
    with _client(runtime.settings) as client:
        supervisor = _token(client, runtime.actor.user_id)
        resident = _token(client, runtime.graph.employees.resident_id)
        property_id = runtime.graph.employees.property_1_id
        before = business_snapshot(runtime)
        response = client.post('/api/v1/supervisor/work-order-agent',
            json={'property_id': property_id, 'question': 'help'}, headers=_auth(supervisor, 'agent-http-ok'))
        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['status'] == 'completed' and data['model_calls'] == 0
        assert data['property_id'] == property_id
        assert data['items'][0]['work_order_id'] == runtime.local_id
        denied = client.post('/api/v1/supervisor/work-order-agent',
            json={'property_id': property_id, 'question': 'help'}, headers=_auth(resident, 'agent-http-resident'))
        assert denied.status_code == 403, denied.text
        foreign = client.post('/api/v1/supervisor/work-order-agent',
            json={'property_id': runtime.graph.employees.property_2_id, 'question': 'help'},
            headers=_auth(supervisor, 'agent-http-foreign'))
        assert foreign.status_code == 403, foreign.text
        assert business_snapshot(runtime) == before
