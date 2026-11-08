"""Bounded supervisor planning over fresh, scoped, read-only evidence."""
from copy import deepcopy
import json
from time import monotonic

from app.core.errors import AppError
from app.core.logging import get_logger
from app.provider.errors import ProviderError

logger = get_logger(__name__)


class SupervisorAgentService:
    def __init__(self, *, tools, planner, max_steps=7, budget_seconds=30.0, clock=monotonic):
        if type(max_steps) is not int or not 1 <= max_steps <= 7:
            raise ValueError('max_steps must be within 1..7')
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 30:
            raise ValueError('budget_seconds must be within 0..30')
        self.tools = tools
        self.planner = planner
        self.max_steps = max_steps
        self.budget_seconds = budget_seconds
        self.clock = clock

    def run(self, *, question: str, request_id: str) -> dict:
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
            raise AppError(code='AGENT.INVALID_INPUT', message='Question is invalid', status_code=422)
        start = self.clock()
        observations, trace = [], []
        queue = None
        diagnoses = {}
        calls = 0
        initial_attempts = getattr(self.planner, 'provider_attempts', 0)

        def remaining():
            return self.budget_seconds - (self.clock() - start)

        def outcome(status, code=None, items=None):
            model_calls = getattr(self.planner, 'provider_attempts', 0) - initial_attempts
            logger.info('supervisor_agent_finished', request_id=request_id,
                        agent_status=status, error_code=code, model_calls=model_calls, planner_steps=calls,
                        tools=[row['action'] for row in trace])
            return {'status': status, 'error_code': code, 'items': items or [],
                    'request_id': request_id, 'model_calls': model_calls, 'planner_steps': calls,
                    'call_count_semantics': 'Provider attempts, not successful HTTP responses; deterministic stub is zero.',
                    'execution_mode': self.planner.mode, 'trace': trace,
                    'scope': 'First queue page, at most 5 records and 3 diagnoses; human decision required.'}

        for _ in range(self.max_steps):
            self.tools.authorize()
            if remaining() <= 0:
                return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
            calls += 1
            targets = [{'work_order_id': work_id, 'version': version}
                       for work_id, version in (queue or {}).items()
                       if work_id not in diagnoses] if len(diagnoses) < 3 else []
            candidates = [{'work_order_id': work_id,
                           'fact_codes': [fact['code'] for fact in data['facts']]}
                          for work_id, data in diagnoses.items() if data['facts']]
            actions = ['queue'] if queue is None else (
                (['diagnose'] if targets else []) +
                (['finish'] if not queue or candidates else []))
            constraints = {'available_actions': actions, 'diagnose_targets': targets,
                           'finish_candidates': candidates,
                           'remaining_steps': self.max_steps - calls + 1,
                           'remaining_diagnoses': 3 - len(diagnoses),
                           'max_finish_items': 3, 'max_fact_codes_per_item': 4}
            try:
                command = self.planner.choose(
                    deepcopy({'question': question, 'observations': observations,
                              'constraints': constraints}),
                    timeout_seconds=remaining())
            except ProviderError:
                return outcome('unavailable', 'AGENT.PROVIDER_FAILED')
            if remaining() <= 0:
                return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
            self.tools.authorize()
            if remaining() <= 0:
                return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
            try:
                if not isinstance(command, dict) or len(json.dumps(command, allow_nan=False)) > 8000:
                    raise ValueError('Invalid command')
                action = command.get('action')
                if action == 'queue':
                    if set(command) != {'action'} or queue is not None:
                        raise ValueError('Repeated queue or unexpected parameters')
                    records = self.tools.queue()
                    if len(records) > 5:
                        raise ValueError('Queue exceeded limit')
                    queue = {row['id']: row['version'] for row in records}
                    observations.append({'tool': 'queue', 'result': records})
                elif action == 'diagnose':
                    if set(command) != {'action', 'work_order_id', 'version'} or queue is None:
                        raise ValueError('Queue required before diagnosis')
                    work_id, version = command['work_order_id'], command['version']
                    if type(work_id) is not int or type(version) is not int:
                        raise ValueError('Integer ID and version required')
                    if work_id not in queue or queue[work_id] != version or work_id in diagnoses or len(diagnoses) >= 3:
                        raise ValueError('Unobserved, stale or repeated target')
                    data = self.tools.diagnose(work_id, version)
                    diagnoses[work_id] = data
                    observations.append({'tool': 'diagnose', 'result': data})
                elif action == 'finish':
                    if set(command) != {'action', 'items'} or queue is None:
                        raise ValueError('Queue required before finishing')
                    selections = command['items']
                    if not isinstance(selections, list) or len(selections) > 3 or (queue and not selections):
                        raise ValueError('Invalid final selection')
                    selected_ids, items = set(), []
                    for selection in selections:
                        if not isinstance(selection, dict) or set(selection) != {'work_order_id', 'fact_codes'}:
                            raise ValueError('Unsupported final fields')
                        work_id, codes = selection['work_order_id'], selection['fact_codes']
                        if type(work_id) is not int or work_id not in diagnoses or work_id in selected_ids:
                            raise ValueError('Undiagnosed or duplicated result')
                        if not isinstance(codes, list) or not 1 <= len(codes) <= 4 or any(type(c) is not str for c in codes):
                            raise ValueError('Invalid evidence references')
                        if len(set(codes)) != len(codes):
                            raise ValueError('Repeated evidence')
                        selected_ids.add(work_id)
                        previous = diagnoses[work_id]
                        current = self.tools.diagnose(work_id, queue[work_id])
                        for key in ('work_order_id', 'property_id', 'version', 'facts', 'suggested_actions'):
                            if current.get(key) != previous.get(key):
                                raise AppError(code='AGENT.EVIDENCE_CHANGED', message='Evidence changed; refresh first', status_code=409)
                        facts = {fact['code']: fact for fact in current['facts']}
                        if any(code not in facts for code in codes):
                            raise ValueError('Unknown evidence code')
                        items.append({'work_order_id': work_id, 'version': queue[work_id],
                                      'facts': [facts[code] for code in codes],
                                      'suggested_actions': current['suggested_actions']})
                    self.tools.authorize()
                    if remaining() <= 0:
                        return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
                    trace.append({'step': calls, 'action': 'finish', 'status': 'ok'})
                    return outcome('completed', items=items)
                else:
                    raise ValueError('Unknown action')
            except (ValueError, TypeError, KeyError):
                return outcome('unavailable', 'AGENT.INVALID_ACTION')
            trace.append({'step': calls, 'action': action, 'status': 'ok'})
            if remaining() <= 0:
                return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
        return outcome('unavailable', 'AGENT.BUDGET_EXHAUSTED')
