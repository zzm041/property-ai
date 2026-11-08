"""Observe the durable path, without broker, database or model calls."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.errors import AiErrorCode
from app.provider.errors import ProviderTimeoutError
from app.service.task_intent import ANALYZE, IntentLeaseLost
from app.worker import task_intents as worker


@pytest.fixture
def harness(monkeypatch):
    from app.service import analysis
    from app.repository import work_order
    from app.worker import tasks
    lease = dict(id=3, operation=ANALYZE, resource_id=17, tenant_id=7, property_id=9,
                 task_id='task-durable', request_id='request-durable', attempt=2,
                 token='SECRET_LEASE_TOKEN', resource_version=4)
    session, service, repo, log, intents = (MagicMock() for _ in range(5))
    session.info = {}
    session.__enter__.return_value = session
    repo.get_work_order_for_update.return_value = NS(id=17, tenant_id=7, property_id=9,
                                                   current_assignment_id=None)
    repo.latest_analysis_attempt.return_value = None
    repo.attachment_completion_request.return_value = None
    service.analyze_work_order.return_value = NS(id=29, status='COMPLETED', error_code=None)
    intents.claim_execution.return_value = lease
    intents.finish.return_value = True
    session.get.return_value = NS(status='SUCCEEDED')
    monkeypatch.setattr(worker, 'Session', lambda *a, **kw: session)
    monkeypatch.setattr(worker, 'TaskIntentService', lambda *a, **kw: intents)
    monkeypatch.setattr(worker, 'begin_owned_transaction', lambda *a: nullcontext())
    monkeypatch.setattr(worker, 'require_execution_lease', lambda *a: None)
    monkeypatch.setattr(worker.threading, 'Thread', MagicMock())
    monkeypatch.setattr(worker, 'logger', log)
    monkeypatch.setattr(analysis, 'AnalysisService', lambda *a, **kw: service)
    monkeypatch.setattr(work_order, 'WorkOrderRepository', lambda *a: repo)
    monkeypatch.setattr(tasks, '_record_analysis_attempt_outcome', MagicMock())
    return NS(lease=lease, session=session, service=service, log=log, intents=intents)


def run(h):
    return worker.execute_intent(None, NS(durable_tasks_enabled=True), 3, 'task-durable')


def event(h, name):
    return next(c.kwargs for c in h.log.info.call_args_list if c.args[0] == name)


@pytest.mark.parametrize('status,outcome', [
    ('COMPLETED', 'COMPLETED'), ('LOW_CONFIDENCE', 'LOW_CONFIDENCE'),
    ('STALE', 'STALE'), ('FAILED', 'FAILED'), (None, 'NO_RESULT'),
])
def test_actual_durable_analysis_is_linked(harness, status, outcome):
    h = harness
    h.service.analyze_work_order.return_value = None if status is None else NS(
        id=29, status=status, error_code=AiErrorCode.INVALID_OUTPUT)
    run(h)
    for name in ('analysis_task_started', 'analysis_task_finished', 'task_intent_started', 'task_intent_finished'):
        row = event(h, name)
        assert row['request_id'] == 'request-durable' and row['task_id'] == 'task-durable'
        assert row['intent_id'] == 3 and row['attempt'] == 2
    end = event(h, 'analysis_task_finished')
    assert end['work_order_id'] == 17 and end['outcome'] == outcome
    assert end['analysis_id'] == (None if status is None else 29)
    assert end['duration_ms'] >= 0
    assert 'SECRET_LEASE_TOKEN' not in str(h.log.mock_calls)


@pytest.mark.parametrize('failure,analysis_outcome,stored', [
    (ProviderTimeoutError('PRIVATE_PROVIDER_BODY'), 'PROVIDER_FAILED', 'RETRY_PENDING'),
    (OperationalError('PRIVATE_SQL', {}, Exception(2013, 'PRIVATE_DB')), 'DATABASE_FAILED', 'RETRY_PENDING'),
    (IntentLeaseLost(), 'LEASE_LOST', 'LEASE_LOST'),
    (ValueError('PRIVATE_UNEXPECTED'), 'ERROR', 'FAILED'),
])
def test_failed_analysis_and_persisted_execution_are_distinct(harness, failure, analysis_outcome, stored):
    h = harness
    h.service.analyze_work_order.side_effect = failure
    h.session.get.return_value = NS(status=stored)
    assert run(h) == stored
    assert event(h, 'analysis_task_finished')['outcome'] == analysis_outcome
    assert event(h, 'task_intent_finished')['outcome'] == stored
    assert 'PRIVATE_' not in str(h.log.mock_calls)


@pytest.mark.parametrize('broken_log', [False, True])
def test_finish_db_failure_not_reported_as_persisted_success(harness, broken_log):
    h = harness
    h.intents.finish.side_effect = OperationalError('PRIVATE_SQL', {}, Exception(2013, 'PRIVATE_DB'))
    if broken_log:
        h.log.info.side_effect = h.log.error.side_effect = RuntimeError('logger failed')
    assert run(h) == 'DB_UNAVAILABLE_LEASE_RECOVERY_REQUIRED'
    if not broken_log:
        assert event(h, 'task_intent_finished')['outcome'] == 'DB_UNAVAILABLE_LEASE_RECOVERY_REQUIRED'


def test_logger_failure_cannot_prevent_permanent_failure_writeback(harness):
    h = harness
    h.service.analyze_work_order.side_effect = ValueError('PRIVATE_UNEXPECTED')
    h.log.info.side_effect = h.log.error.side_effect = RuntimeError('logger failed')
    h.session.get.return_value = NS(status='FAILED')
    assert run(h) == 'FAILED'
    assert h.intents.finish.call_args.kwargs == dict(error='TASK.PERMANENT_FAILURE', retryable=False)


def test_duplicate_without_lease_does_not_claim_an_analysis_attempt(harness):
    h = harness
    h.intents.claim_execution.return_value = None
    assert run(h) == 'IGNORED'
    h.service.analyze_work_order.assert_not_called()
    h.log.info.assert_not_called()


def test_prefork_logging_survives_celery_stdout_redirection():
    # Real Celery stream proxy reproduces production prefork startup; subprocess
    # avoids changing pytest's logging or stdout for the remaining tests.
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = '''
import logging, sys
from celery.utils.log import LoggingProxy
from app.worker.celery_app import _configure_worker_logging
from app.core.logging import get_logger
sys.stdout = LoggingProxy(logging.getLogger('celery.redirected'), loglevel=logging.WARNING)
_configure_worker_logging()
get_logger('app.worker.task_intents').info('prefork_trace_probe', request_id='synthetic-request', access_token='PRIVATE_TOKEN')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    completed = subprocess.run([sys.executable, '-B', '-c', script], env=env,
                               capture_output=True, text=True, timeout=20, check=True)
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith('{')]
    assert len(events) == 1
    assert events[0]['event'] == 'prefork_trace_probe'
    assert events[0]['service'] == 'worker' and events[0]['request_id'] == 'synthetic-request'
    assert 'PRIVATE_TOKEN' not in completed.stdout + completed.stderr
