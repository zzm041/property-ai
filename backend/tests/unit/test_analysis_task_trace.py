"""Task attempt observability, without a database, broker or model."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.errors import AiErrorCode
from app.worker import tasks
from app.provider.errors import ProviderTimeoutError


@pytest.fixture
def harness(monkeypatch):
    session, engine, service, log = (MagicMock() for _ in range(4))
    monkeypatch.setattr(tasks, 'get_settings', lambda: NS(active_database_url='synthetic'))
    monkeypatch.setattr(tasks, 'create_database_engine', lambda url: engine)
    monkeypatch.setattr(tasks, 'Session', lambda **kw: session)
    monkeypatch.setattr(tasks, 'AnalysisService', lambda *a, **kw: service)
    monkeypatch.setattr(tasks, '_record_analysis_attempt_outcome', MagicMock())
    monkeypatch.setattr(tasks, 'logger', log)
    retry = MagicMock(side_effect=RuntimeError('controlled retry'))
    monkeypatch.setattr(tasks.analyze_work_order_task, 'retry', retry)
    tasks.analyze_work_order_task.push_request(id='task-trace', retries=0, headers={})
    yield NS(session=session, engine=engine, service=service, log=log, retry=retry)
    tasks.analyze_work_order_task.pop_request()


def events(h):
    return {call.args[0]: call.kwargs for call in h.log.info.call_args_list}


@pytest.mark.parametrize('status,outcome', [
    ('COMPLETED', 'COMPLETED'), ('LOW_CONFIDENCE', 'LOW_CONFIDENCE'),
    ('STALE', 'STALE'), ('FAILED', 'FAILED'), (None, 'NO_RESULT'),
])
def test_attempt_links_and_business_outcome(harness, status, outcome):
    h = harness
    h.service.analyze_work_order.return_value = None if status is None else NS(
        id=29, status=status, error_code=AiErrorCode.INVALID_OUTPUT)
    tasks.analyze_work_order_task.run(17, 'request-trace')
    start, end = (events(h)[name] for name in ('analysis_task_started', 'analysis_task_finished'))
    for event in (start, end):
        assert event['request_id'] == 'request-trace'
        assert event['task_id'] == 'task-trace' and event['work_order_id'] == 17
        assert event['attempt'] == 1
    assert end['outcome'] == outcome
    assert end['analysis_id'] == (None if status is None else 29)
    assert end['duration_ms'] >= 0
    h.session.close.assert_called_once()
    h.engine.dispose.assert_called_once()


def test_provider_retry_is_observable_not_reported_success(harness):
    h = harness
    h.service.analyze_work_order.return_value = NS(id=29, status='FAILED', error_code=AiErrorCode.TIMEOUT)
    with pytest.raises(RuntimeError, match='controlled retry'):
        tasks.analyze_work_order_task.run(17, 'request-trace')
    end = events(h)['analysis_task_finished']
    assert end['outcome'] == 'PROVIDER_FAILED' and end['analysis_id'] == 29
    assert h.retry.call_args.kwargs['countdown'] == 1


def test_unexpected_failure_preserved_and_content_excluded(harness):
    h = harness
    h.service.analyze_work_order.side_effect = ValueError('private report text phone address')
    with pytest.raises(ValueError, match='private report'):
        tasks.analyze_work_order_task.run(17, 'request-trace')
    end = events(h)['analysis_task_finished']
    assert end['outcome'] == 'ERROR' and end['analysis_id'] is None
    assert 'private report' not in str(h.log.mock_calls)
    h.session.close.assert_called_once()
    h.engine.dispose.assert_called_once()


@pytest.mark.parametrize('retries,outcome', [(0, 'DATABASE_FAILED'), (2, 'DATABASE_RETRY_EXHAUSTED')])
def test_database_failure_attempt_link(harness, monkeypatch, retries, outcome):
    h = harness
    tasks.analyze_work_order_task.request.retries = retries
    h.service.analyze_work_order.side_effect = OperationalError('private SQL', {}, Exception(2013, 'private DB detail'))
    monkeypatch.setattr(tasks, '_persist_database_exhaustion', lambda *a: True)
    with pytest.raises((RuntimeError, tasks.AnalysisDatabaseRetryExhausted)):
        tasks.analyze_work_order_task.run(17, 'request-trace')
    end = events(h)['analysis_task_finished']
    assert end['outcome'] == outcome and end['attempt'] == retries + 1
    assert 'private' not in str(h.log.mock_calls)


def test_broken_logging_cannot_replace_provider_retry(harness):
    h = harness
    h.log.info.side_effect = h.log.warning.side_effect = RuntimeError('logger broken')
    h.service.analyze_work_order.side_effect = ProviderTimeoutError('private provider detail')
    with pytest.raises(RuntimeError, match='controlled retry'):
        tasks.analyze_work_order_task.run(17, 'request-trace')
    h.retry.assert_called_once()
    h.session.close.assert_called_once()
    h.engine.dispose.assert_called_once()


def test_exhaustion_persistence_and_logger_failure_still_returns_false(monkeypatch):
    log = MagicMock()
    log.error.side_effect = RuntimeError('logger broken')
    monkeypatch.setattr(tasks, 'logger', log)
    monkeypatch.setattr(tasks, 'Session', MagicMock(side_effect=OperationalError(
        'private SQL', {}, Exception(2013, 'private DB detail'))))
    assert tasks._persist_database_exhaustion(None, None, 17, 'request-trace', {}) is False


def test_attempt_journal_and_logger_failure_does_not_fail_completed_result(monkeypatch):
    from app.worker import failed_tasks
    log = MagicMock()
    log.warning.side_effect = RuntimeError('logger broken')
    monkeypatch.setattr(tasks, 'logger', log)
    monkeypatch.setattr(failed_tasks, 'complete_attempt', MagicMock(side_effect=RuntimeError('journal unavailable')))
    tasks._record_analysis_attempt_outcome('task-trace', NS(status='COMPLETED', error_code=None))
