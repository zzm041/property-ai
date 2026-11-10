"""Provider, session and HTTP boundaries for the supervisor agent."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.dependencies.auth import get_current_principal
from app.core.config import Settings, get_settings
from app.provider.supervisor_agent import SupervisorPlanner
from app.service.supervisor_agent_tools import SupervisorReadTools


def test_tool_outputs_are_minimal_and_sessions_close_before_return(monkeypatch):
    import app.service.supervisor_agent_tools as module
    sessions = []
    def session_factory():
        session = MagicMock(); sessions.append(session); return session
    service = MagicMock()
    service.list_supervisor_queue.return_value = [NS(id=11, version=2, status='PENDING_ASSIGNMENT',
        priority='HIGH', process_reason=None, contact_phone='PRIVATE', house_id=123)]
    service.is_overdue.return_value = True
    service.diagnose_work_order.return_value.model_dump.return_value = {
        'work_order_id': 11, 'property_id': 9, 'version': 2, 'facts': [], 'suggested_actions': [],
        'observed_at': 'timestamp', 'contact_phone': 'PRIVATE'}
    monkeypatch.setattr(module, 'WorkOrderService', lambda session, settings: service)
    tools = SupervisorReadTools(session_factory=session_factory, settings=Settings(), actor='actor', property_id=9)
    queue = tools.queue(); diagnosis = tools.diagnose(11, 2); tools.authorize()
    assert 'PRIVATE' not in str(queue) + str(diagnosis)
    assert 'house_id' not in queue[0]
    assert all(session.close.call_count == 1 for session in sessions)
    assert service.list_supervisor_queue.call_args.kwargs['property_id'] == 9


def test_provider_uses_separate_configuration_and_remaining_budget(monkeypatch):
    import app.provider.supervisor_agent as module
    captured = {}
    class Provider:
        def __init__(self, **kwargs): captured.update(kwargs)
        def structured_completion(self, **kwargs): captured['request'] = kwargs; return {'action': 'queue'}
    monkeypatch.setattr(module, 'QwenLLMProvider', Provider)
    settings = Settings(supervisor_agent_provider='production', supervisor_agent_base_url='http://127.0.0.1:11435/v1',
                        supervisor_agent_model='approved-model', supervisor_agent_api_key='test-only')
    planner = SupervisorPlanner(settings)
    assert planner.choose({'question': 'help', 'observations': []}, timeout_seconds=2)['action'] == 'queue'
    assert captured['timeout_seconds'] == 2
    assert captured['max_output_tokens'] == 512
    assert captured['model'] == 'approved-model'
    assert planner.provider_attempts == 1
    assert captured['request']['prompt_version'] == 'supervisor-read-agent-v2-legal-choices'


def test_development_mode_is_explicit_and_offline():
    planner = SupervisorPlanner(Settings())
    assert planner.mode == 'deterministic_stub'
    assert planner.choose({'question': 'help', 'observations': []}, timeout_seconds=1) == {'action': 'queue'}
    assert planner.provider_attempts == 0


def api_client(settings):
    from app.api.v1.supervisor_work_orders import router
    from app.core.errors import AppError
    from fastapi.responses import JSONResponse
    app = FastAPI(); app.include_router(router)
    @app.exception_handler(AppError)
    def error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={'code': exc.code})
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_principal] = lambda: NS(user_id=1)
    return TestClient(app)


def test_endpoint_is_disabled_by_default():
    with api_client(Settings()) as client:
        response = client.post('/supervisor/work-order-agent', json={'property_id': 9, 'question': 'help'})
    assert response.status_code == 503
    assert response.json()['code'] == 'AGENT.DISABLED'


def test_endpoint_rejects_scope_header_and_unknown_fields():
    with api_client(Settings(supervisor_agent_enabled=True)) as client:
        mismatch = client.post('/supervisor/work-order-agent', json={'property_id': 9, 'question': 'help'},
                               headers={'X-Property-Id': '10'})
        extra = client.post('/supervisor/work-order-agent', json={'property_id': 9, 'question': 'help', 'tenant_id': 99})
    assert mismatch.status_code == 422 and extra.status_code == 422


def test_endpoint_executes_bounded_loop_and_marks_stub_calls(monkeypatch):
    import app.api.v1.supervisor_work_orders as api
    from app.service.supervisor_agent import SupervisorAgentService
    class Tools:
        def authorize(self): pass
        def queue(self): return []
    def build(*, actor, property_id, settings):
        assert actor.user_id == 1 and property_id == 9
        return SupervisorAgentService(tools=Tools(), planner=SupervisorPlanner(settings))
    monkeypatch.setattr(api, 'build_supervisor_agent', build)
    with api_client(Settings(supervisor_agent_enabled=True)) as client:
        response = client.post('/supervisor/work-order-agent', json={'property_id': 9, 'question': '需要关注哪些单？'})
    assert response.status_code == 200
    data = response.json()['data']
    assert data['status'] == 'completed' and data['items'] == []
    assert data['execution_mode'] == 'deterministic_stub'
    assert data['model_calls'] == 0 and data['planner_steps'] == 2


def test_endpoint_denies_missing_authentication():
    with api_client(Settings(supervisor_agent_enabled=True)) as client:
        client.app.dependency_overrides.pop(get_current_principal)
        response = client.post('/supervisor/work-order-agent', json={'property_id': 9, 'question': 'help'})
    assert response.status_code == 401


def test_capability_checks_current_scope_without_model_call(monkeypatch):
    import app.api.v1.supervisor_work_orders as api
    from app.core.errors import AppError
    tools = MagicMock()
    def build(*, actor, property_id, settings):
        assert property_id == 9
        return NS(tools=tools)
    monkeypatch.setattr(api, 'build_supervisor_agent', build)
    with api_client(Settings()) as client:
        response = client.get('/supervisor/work-order-agent/capabilities?property_id=9')
        assert response.status_code == 200
        assert response.json()['data'] == {'property_id': 9, 'enabled': False}
        tools.authorize.assert_called_once()
        tools.authorize.side_effect = AppError(code='IAM.FORBIDDEN', message='Denied', status_code=403)
        denied = client.get('/supervisor/work-order-agent/capabilities?property_id=9')
        assert denied.status_code == 403
