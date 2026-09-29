"""Bounded operation retry and journal fencing, no database/queue fixtures."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.database import retryable_mysql_analysis_error_code
from app.core.errors import AiErrorCode
from app.service.analysis import AnalysisService
from app.service.analysis_attempt import (
    accepts_attempt,
    claim_attempt,
    owns_attempt,
    provider_checkpoint_payload,
    read_provider_checkpoint,
)
from app.worker.tasks import analyze_work_order_task, AnalysisDatabaseRetryExhausted


def db_error(code=2013):
    return OperationalError('controlled statement', {}, Exception(code, 'synthetic transient'))


def test_original_detail_display_context_still_returns_real_repository_result():
    from app.repository.work_order import WorkOrderRepository
    session=MagicMock(); session.scalar.return_value='Asia/Shanghai'
    session.execute.return_value.all.return_value=[(7,'025员工')]
    assert WorkOrderRepository(session).get_display_context(record()) == {
        'property_timezone':'Asia/Shanghai','assignee_names':{7:'025员工'}}


def test_attempt_lookup_is_current_locking_read():
    from app.repository.work_order import WorkOrderRepository
    session=MagicMock(); WorkOrderRepository(session).latest_analysis_attempt(30)
    sql=str(session.scalar.call_args.args[0])
    assert 'FOR UPDATE' in sql and 'work_order_logs.work_order_id' in sql


@pytest.mark.parametrize('code', [1205, 1213, 2006, 2013])
def test_precise_analysis_whitelist(code):
    assert retryable_mysql_analysis_error_code(db_error(code)) == code


@pytest.mark.parametrize('code', [1045, 1062, 1146, 2003, 'unstructured'])
def test_no_generic_or_auth_schema_retry(code):
    assert retryable_mysql_analysis_error_code(db_error(code)) is None


def record(status='PENDING_ANALYSIS', version=1):
    return NS(id=30, tenant_id=1, property_id=2, status=status, version=version,
              process_reason='NONE', current_assignment_id=None)


def claim(attempt=1, task_id='task-025', version=1):
    return dict(task_id=task_id, attempt=attempt, execution_token='execution-025',
                analysis_id=7, analysis_version=1, input_hash='hash',
                work_order_version=version, from_status='PENDING_ANALYSIS')


def context(attempt=2):
    return dict(task_id='task-025', attempt=attempt, execution_token='next-execution',
                expected_version=1, claim=claim())


def repo(payload=None):
    r = MagicMock()
    r.latest_analysis_attempt.return_value = NS(payload_json=payload) if payload else None
    return r


@pytest.mark.parametrize('status,version,accepted', [
    ('PENDING_ANALYSIS', 1, True), ('PENDING_ASSIGNMENT', 2, True),
    ('PENDING_ANALYSIS', 2, False), ('PENDING_ASSIGNMENT', 3, False),
])
def test_retry_accepts_only_own_transition_not_changed_input(status, version, accepted):
    assert accepts_attempt(repo(claim()), record(status, version), context()) is accepted


def test_newer_owner_or_attempt_fences_delayed_delivery():
    assert not accepts_attempt(repo(claim(task_id='new-task')), record(), context())
    assert not accepts_attempt(repo(claim(attempt=3)), record(), context(attempt=2))


def test_unacknowledged_prepare_commit_is_read_from_journal():
    assert accepts_attempt(repo(claim()), record(), context())
    # Rolled-back first prepare has no journal; original version can be claimed.
    assert accepts_attempt(repo(), record(), context())
    assert not accepts_attempt(repo(), record(version=2), context())


def test_claim_and_execution_token_are_durable_existing_history():
    r = repo(); c = context(attempt=1)
    snap = NS(analysis_id=7, analysis_version=1, input_hash='hash')
    assert claim_attempt(r, record(), snap, c, 'request-025') is snap
    log = r.add_log.call_args.args[0]
    assert log.event_code == 'AI_TASK_ATTEMPT'
    assert set(log.payload_json) == {'task_id','attempt','execution_token','analysis_id',
                                     'analysis_version','input_hash','work_order_version','from_status'}
    r.latest_analysis_attempt.return_value = log
    analysis = NS(id=7, analysis_version=1, input_hash='hash')
    assert owns_attempt(r, record(), analysis, c)
    r.latest_analysis_attempt.return_value = NS(payload_json={**log.payload_json, 'execution_token':'duplicate-worker'})
    assert not owns_attempt(r, record(), analysis, c)


def test_provider_checkpoint_is_bound_to_exact_task_input_model_and_attempt():
    snap = NS(analysis_id=7, analysis_version=1, input_hash='hash')
    current = context(attempt=2)
    payload = provider_checkpoint_payload(snap, current, 'VLM', 'qwen-fixed', 'visible damage')
    assert read_provider_checkpoint(payload, snap, current, 'VLM', 'qwen-fixed') == 'visible damage'
    assert read_provider_checkpoint(payload, snap, {**current, 'task_id':'other'}, 'VLM', 'qwen-fixed') is None
    assert read_provider_checkpoint(payload, NS(**{**snap.__dict__, 'input_hash':'changed'}), current, 'VLM', 'qwen-fixed') is None
    assert read_provider_checkpoint(payload, snap, current, 'VLM', 'different-model') is None
    assert read_provider_checkpoint({**payload, 'attempt':3}, snap, current, 'VLM', 'qwen-fixed') is None
    assert read_provider_checkpoint({**payload, 'value':'tampered'}, snap, current, 'VLM', 'qwen-fixed') is None


def test_saved_analysis_dispatches_outside_apply_and_without_provider_recall():
    s = AnalysisService.__new__(AnalysisService)
    s._prepare = MagicMock(return_value=NS(skipped=True, analysis_id=7))
    s._retry_auto_dispatch_from_existing = MagicMock()
    s._ai = MagicMock(); s._call_providers = MagicMock()
    s.analyze_work_order(work_order_id=30, request_id='retry', task_context=context())
    s._call_providers.assert_not_called()
    s._retry_auto_dispatch_from_existing.assert_called_once()


def test_apply_commit_precedes_separate_dispatch():
    s = AnalysisService.__new__(AnalysisService)
    order = []
    s._prepare = MagicMock(return_value=NS(skipped=False))
    s._call_providers = MagicMock()
    s._apply = MagicMock(side_effect=lambda **kw: (order.append('apply-committed') or NS(id=7,status='COMPLETED')))
    s._retry_auto_dispatch_from_existing = MagicMock(side_effect=lambda **kw: order.append('dispatch'))
    s.analyze_work_order(work_order_id=30, request_id='split')
    assert order == ['apply-committed', 'dispatch']


class Scheduled(Exception):
    pass


@pytest.mark.parametrize('retries,delay', [(0,1),(1,2)])
def test_worker_discards_session_and_retries_same_task_with_fence(monkeypatch, retries, delay):
    engine, session, service = MagicMock(), MagicMock(), MagicMock()
    failure = db_error()
    service.analyze_work_order.side_effect = failure
    monkeypatch.setattr('app.worker.tasks.create_database_engine', lambda url: engine)
    monkeypatch.setattr('app.worker.tasks.Session', lambda **kw: session)
    monkeypatch.setattr('app.worker.tasks.AnalysisService', lambda *a, **kw: service)
    retry = MagicMock(side_effect=Scheduled)
    monkeypatch.setattr(analyze_work_order_task, 'retry', retry)
    analyze_work_order_task.push_request(id='task-025', retries=retries, headers={'analysis_expected_version':1})
    try:
        with pytest.raises(Scheduled): analyze_work_order_task.run(30,'retry')
    finally: analyze_work_order_task.pop_request()
    session.invalidate.assert_called_once(); session.close.assert_called_once(); engine.dispose.assert_called_once()
    assert retry.call_args.kwargs['countdown'] == delay
    assert retry.call_args.kwargs['headers']['analysis_expected_version'] == 1
    assert service.analyze_work_order.call_args.kwargs['task_context']['attempt'] == retries+1


def test_budget_exhaustion_uses_fresh_failure_boundary_not_fourth_retry(monkeypatch):
    session, service = MagicMock(), MagicMock()
    service.analyze_work_order.side_effect = db_error()
    monkeypatch.setattr('app.worker.tasks.create_database_engine', lambda url: MagicMock())
    monkeypatch.setattr('app.worker.tasks.Session', lambda **kw: session)
    monkeypatch.setattr('app.worker.tasks.AnalysisService', lambda *a, **kw: service)
    persist = MagicMock(return_value=False)
    monkeypatch.setattr('app.worker.tasks._persist_database_exhaustion', persist)
    retry = MagicMock(); monkeypatch.setattr(analyze_work_order_task, 'retry', retry)
    analyze_work_order_task.push_request(id='task-025', retries=2, headers={})
    try:
        with pytest.raises(AnalysisDatabaseRetryExhausted) as exc:
            analyze_work_order_task.run(30,'exhaust')
    finally: analyze_work_order_task.pop_request()
    assert exc.value.error_code == AiErrorCode.DATABASE_RETRY_EXHAUSTED
    assert persist.call_args.args[-1]['attempt'] == 3
    retry.assert_not_called()


@pytest.mark.parametrize('status,version,newer,allowed', [
    ('PENDING_ANALYSIS',1,False,True), ('PENDING_ASSIGNMENT',2,False,True),
    ('CANCELLED',2,False,False), ('CLOSED',7,False,False),
    ('PENDING_ANALYSIS',2,False,False), ('PENDING_ANALYSIS',1,True,False),
])
def test_exhaustion_never_overwrites_terminal_new_version_or_attempt(monkeypatch,status,version,newer,allowed):
    monkeypatch.setattr('app.service.analysis.begin_owned_transaction',lambda _:nullcontext())
    s = AnalysisService.__new__(AnalysisService); s._session=MagicMock()
    s._session.info = {}  # This legacy-path fixture has no durable execution lease.
    s._work_orders=repo(claim(attempt=4 if newer else 1)); s._ai=MagicMock()
    s._work_orders.get_work_order_for_update.return_value=record(status,version)
    s._work_orders.get_active_assignment.return_value=None
    analysis=NS(id=7,input_hash='hash',status='COMPLETED' if status=='PENDING_ASSIGNMENT' else 'PROCESSING',error_code=None)
    s._ai.get_current_analysis_for_update.return_value=analysis
    s._append_failure_audit=MagicMock()
    assert s.record_database_exhaustion(work_order_id=30,request_id='final',context=context(3)) is allowed
    if allowed:
        assert s._work_orders.add_log.call_args.args[0].event_code=='AI_TASK_FAILED'
        assert analysis.status==('COMPLETED' if status=='PENDING_ASSIGNMENT' else 'FAILED')
    else: s._work_orders.add_log.assert_not_called()


def test_db_unavailable_does_not_claim_failure_was_persisted(monkeypatch):
    from app.worker.tasks import _persist_database_exhaustion
    monkeypatch.setattr('app.worker.tasks.Session',MagicMock(side_effect=db_error()))
    logger=MagicMock();monkeypatch.setattr('app.worker.tasks.logger',logger)
    assert _persist_database_exhaustion(MagicMock(),MagicMock(),30,'req',context(3)) is False
    assert logger.error.call_args.kwargs['db_persisted'] is False


def test_fenced_committed_analysis_survives_changed_historical_context(monkeypatch):
    from app.core.config import Settings
    from app.core.time import utc_now
    monkeypatch.setattr('app.service.analysis.begin_owned_transaction',lambda _:nullcontext())
    s=AnalysisService.__new__(AnalysisService);s._session=MagicMock();s._settings=Settings(_env_file=None)
    s._session.info = {}
    s._clock=utc_now;s._task_context=context(2);s._work_orders=repo(claim());s._ai=MagicMock()
    r=record('PENDING_ASSIGNMENT',2);r.house_id=3;r.created_at=utc_now()
    s._work_orders.get_work_order_for_update.return_value=r
    s._work_orders.get_content.return_value=NS(text='unchanged tap leak',extra_notes='',asr_text=None)
    s._ai.list_media_with_files.return_value=()
    s._ai.get_analysis_context.return_value={'recent_house_orders':[{'status':'PROCESSING'}]}
    s._ai.get_building_id.return_value=4
    s._ai.get_current_analysis_for_update.return_value=NS(id=7,analysis_version=1,status='COMPLETED',input_hash='hash')
    snap=s._prepare(work_order_id=30,request_id='retry-same-input')
    assert snap.skipped and snap.input_hash=='hash'
    assert s._task_context['claim']['work_order_version']==2
    s._ai.add_analysis.assert_not_called()
    r.status='PENDING_ANALYSIS';r.version=3
    assert s._prepare(work_order_id=30,request_id='old-task-after-append') is None


def test_stale_task_completes_existing_tracking_as_ignored_without_replay(monkeypatch):
    from app.worker.tasks import _record_analysis_attempt_outcome
    complete=MagicMock();monkeypatch.setattr('app.worker.failed_tasks.complete_attempt',complete)
    _record_analysis_attempt_outcome('synthetic-task',None)
    complete.assert_called_once_with('synthetic-task','IGNORED_EXPIRED',error_code=None)
