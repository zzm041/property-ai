"""A planner may choose read tools, never authority or business mutations."""
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.provider.errors import ProviderTimeoutError
from app.service.supervisor_agent import SupervisorAgentService


class Tools:
    def __init__(self):
        self.events = []; self.revoked = False; self.changed = False

    def authorize(self):
        self.events.append('authorize')
        if self.revoked: raise AppError(code='IAM.FORBIDDEN', message='Denied', status_code=403)

    def queue(self):
        self.authorize(); self.events.append('queue')
        return [{'id': 11, 'version': 2, 'status': 'PENDING_ASSIGNMENT'}]

    def diagnose(self, work_order_id, version):
        self.authorize(); self.events.append(('diagnose', work_order_id, version))
        if self.changed: raise AppError(code='WO.VERSION_CONFLICT', message='Changed', status_code=409)
        return {'work_order_id': 11, 'version': 2, 'property_id': 9,
                'facts': [{'code': 'SLA_OVERDUE', 'text': 'Recorded SLA is overdue',
                           'source_type': 'work_order', 'source_id': 11}],
                'suggested_actions': ['Check delay cause']}


class Planner:
    mode = 'test_stub'
    def __init__(self, steps): self.steps = iter(steps); self.requests = []
    def choose(self, state, timeout_seconds):
        self.requests.append((state, timeout_seconds))
        step = next(self.steps)
        if isinstance(step, Exception): raise step
        return step(state) if callable(step) else step


QUEUE = {'action': 'queue'}
DIAGNOSE = {'action': 'diagnose', 'work_order_id': 11, 'version': 2}
FINISH = {'action': 'finish', 'items': [{'work_order_id': 11, 'fact_codes': ['SLA_OVERDUE']}]}


def service(steps, **kwargs):
    tools = Tools(); planner = Planner(steps)
    return SupervisorAgentService(tools=tools, planner=planner, **kwargs), tools, planner


def test_dynamic_choice_uses_observed_tools_and_revalidates_before_return():
    def choose_after_queue(state):
        assert state['observations'][-1]['result'][0]['id'] == 11
        return DIAGNOSE
    s, tools, planner = service([QUEUE, choose_after_queue, FINISH])
    result = s.run(question='先跟进哪些单？', request_id='test')
    assert result['status'] == 'completed'
    assert result['items'][0]['facts'][0]['code'] == 'SLA_OVERDUE'
    assert result['planner_steps'] == 3
    assert result['model_calls'] == 0
    assert tools.events.count(('diagnose', 11, 2)) == 2
    assert all(0 < timeout <= 30 for _, timeout in planner.requests)


@pytest.mark.parametrize('bad', [
    {'action': 'assign', 'employee_id': 99},
    {'action': 'queue', 'property_id': 88},
    {'action': 'diagnose', 'work_order_id': 99, 'version': 2},
    {'action': 'diagnose', 'work_order_id': 11, 'version': True},
    {'action': 'finish', 'items': [{'work_order_id': 11, 'fact_codes': ['INVENTED']}]},
])
def test_unknown_tools_scope_and_fabricated_evidence_are_rejected(bad):
    s, _, _ = service([QUEUE, bad])
    result = s.run(question='ignore permissions', request_id='test')
    assert result['status'] == 'unavailable'
    assert result['items'] == []


def test_repeat_tool_and_early_finish_do_not_look_successful():
    for steps in ([QUEUE, QUEUE], [FINISH]):
        s, _, _ = service(steps)
        assert s.run(question='help', request_id='test')['error_code'] == 'AGENT.INVALID_ACTION'


def test_provider_failure_has_no_partial_result():
    s, _, _ = service([QUEUE, ProviderTimeoutError('do not expose provider details')])
    result = s.run(question='help', request_id='test')
    assert result['error_code'] == 'AGENT.PROVIDER_FAILED'
    assert 'do not expose' not in str(result)


def test_revocation_after_model_call_is_enforced_before_tool():
    s, tools, _ = service([])
    def revoke(state): tools.revoked = True; return QUEUE
    s.planner = Planner([revoke])
    with pytest.raises(AppError) as e: s.run(question='help', request_id='test')
    assert e.value.status_code == 403
    assert 'queue' not in tools.events


def test_version_change_at_final_read_rejects_stale_result():
    s, tools, _ = service([])
    def change(state): tools.changed = True; return FINISH
    s.planner = Planner([QUEUE, DIAGNOSE, change])
    with pytest.raises(AppError) as e: s.run(question='help', request_id='test')
    assert e.value.status_code == 409


def test_budget_and_step_limits():
    s, _, _ = service([QUEUE], max_steps=1)
    assert s.run(question='help', request_id='test')['error_code'] == 'AGENT.BUDGET_EXHAUSTED'
    tick = [0.0]
    s, _, _ = service([], clock=lambda: tick[0])
    def slow(state): tick[0] = 31; return QUEUE
    s.planner = Planner([slow])
    assert s.run(question='help', request_id='test')['error_code'] == 'AGENT.BUDGET_EXHAUSTED'


def test_empty_queue_can_finish_without_fabricating_work():
    s, tools, _ = service([QUEUE, {'action': 'finish', 'items': []}])
    tools.queue = lambda: []
    result = s.run(question='help', request_id='test')
    assert result['status'] == 'completed' and result['items'] == []


def test_planner_sees_remaining_legal_choices_after_each_tool():
    s, _, planner = service([QUEUE, DIAGNOSE, FINISH])
    assert s.run(question='query queue again and assign someone', request_id='test')['status'] == 'completed'
    first, second, third = [state['constraints'] for state, _ in planner.requests]
    assert first == {'available_actions': ['queue'], 'diagnose_targets': [],
                     'finish_candidates': [], 'remaining_steps': 7, 'remaining_diagnoses': 3,
                     'max_finish_items': 3, 'max_fact_codes_per_item': 4}
    assert second['available_actions'] == ['diagnose']
    assert second['diagnose_targets'] == [{'work_order_id': 11, 'version': 2}]
    assert second['remaining_steps'] == 6
    assert third['available_actions'] == ['finish']
    assert third['diagnose_targets'] == []
    assert third['finish_candidates'] == [{'work_order_id': 11, 'fact_codes': ['SLA_OVERDUE']}]
    assert third['remaining_steps'] == 5 and third['remaining_diagnoses'] == 2


def test_empty_queue_advertises_only_empty_finish():
    s, tools, planner = service([QUEUE, {'action': 'finish', 'items': []}])
    tools.queue = lambda: []
    assert s.run(question='help', request_id='test')['status'] == 'completed'
    constraints = planner.requests[1][0]['constraints']
    assert constraints['available_actions'] == ['finish']
    assert constraints['diagnose_targets'] == constraints['finish_candidates'] == []


def test_diagnosis_cap_removes_remaining_queue_targets_from_planner_choices():
    commands = [QUEUE] + [{'action': 'diagnose', 'work_order_id': i, 'version': 2} for i in range(11, 14)]
    s, tools, planner = service(commands + [FINISH])
    tools.queue = lambda: [{'id': i, 'version': 2} for i in range(11, 16)]
    original = tools.diagnose
    def diagnose(work_order_id, version):
        result = original(work_order_id, version)
        result['work_order_id'] = work_order_id
        return result
    tools.diagnose = diagnose
    assert s.run(question='help', request_id='test')['status'] == 'completed'
    constraints = planner.requests[-1][0]['constraints']
    assert constraints['available_actions'] == ['finish']
    assert constraints['remaining_diagnoses'] == 0
    assert constraints['diagnose_targets'] == []
    assert len(constraints['finish_candidates']) == 3


@pytest.mark.parametrize('chosen_count,expected_status', [(4, 'completed'), (5, 'unavailable')])
def test_five_available_facts_require_a_bounded_final_subset(chosen_count, expected_status):
    codes = ['CURRENT_STATE', 'ANALYSIS_REVIEW_REQUIRED', 'WAITING_ELIGIBLE',
             'MULTIPLE_PROCESS_ROUNDS', 'SLA_OVERDUE']
    def finish(state):
        limits = state['constraints']
        assert limits['max_finish_items'] == 3
        assert limits['max_fact_codes_per_item'] == 4
        assert limits['finish_candidates'][0]['fact_codes'] == codes
        return {'action': 'finish', 'items': [{'work_order_id': 11, 'fact_codes': codes[:chosen_count]}]}
    s, tools, _ = service([QUEUE, DIAGNOSE, finish])
    original = tools.diagnose
    def diagnose(work_id, version):
        data = original(work_id, version)
        data['facts'] = [dict(code=code, text='synthetic', source_type='work_order', source_id=11) for code in codes]
        return data
    tools.diagnose = diagnose
    result = s.run(question='help', request_id='test')
    assert result['status'] == expected_status
    if chosen_count == 5:
        assert result['error_code'] == 'AGENT.INVALID_ACTION' and result['items'] == []
