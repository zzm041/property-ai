"""Trace behavior must not expose content or change retrieval semantics."""
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, IamErrorCode
from app.provider.errors import ProviderTimeoutError
from app.provider.usage import StructuredCompletion
from app.service import retrieval
from test_retrieval_failure_contract import query_context
from test_retrieval_freshness_contract import context


@pytest.fixture
def trace_log(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(retrieval, 'logger', log)
    return log


def event(log):
    events = [call.kwargs for call in log.info.call_args_list
              if call.args == ('knowledge_query_completed',)]
    assert len(events) == 1
    return events[0]


def test_success_trace_has_phase_counts_usage_without_content(query_context, trace_log):
    ctx = query_context
    ctx.llm.model_version = 'synthetic-model-v1'
    ctx.llm.structured_completion.return_value = StructuredCompletion(
        {'status': 'answered', 'answer': 'synthetic supported answer', 'citation_chunk_ids': [12]}, token_usage=17)
    assert ctx.query().status == 'answered'
    e = event(trace_log)
    assert e['request_id'] == 'unit-query' and e['status'] == 'answered'
    assert (e['retrieved_count'], e['visible_count'], e['selected_count'], e['citation_count']) == (2, 2, 2, 1)
    assert [p['stage'] for p in e['phases']] == ['embed', 'search', 'rerank', 'generate']
    assert all(p['status'] == 'success' and p['latency_ms'] >= 0 for p in e['phases'])
    assert e['phases'][-1]['model_version'] == 'synthetic-model-v1'
    assert e['phases'][-1]['token_usage'] == 17
    assert e['phases'][0]['token_usage'] is None
    for sensitive in ('synthetic question', 'synthetic passage', 'synthetic supported answer', 'paragraph:', 'document 12'):
        assert sensitive not in str(e)


@pytest.mark.parametrize('stage', ['embed', 'search', 'rerank', 'generate'])
def test_provider_failure_reports_actual_stage_without_exception_text(query_context, trace_log, stage):
    ctx = query_context
    method = {'embed': ctx.embedder.embed, 'search': ctx.store.search,
              'rerank': ctx.reranker.rerank, 'generate': ctx.llm.structured_completion}[stage]
    method.side_effect = ProviderTimeoutError('private query secret content')
    result = ctx.query()
    e = event(trace_log)
    failed = [p for p in e['phases'] if p['status'] == 'failed']
    assert [p['stage'] for p in failed] == [stage]
    assert e['rerank_fallback'] is (stage == 'rerank')
    assert e['status'] == result.status
    assert 'private query secret content' not in str(e)
    if stage in {'embed', 'search'}:
        assert not any(p['stage'] == 'generate' for p in e['phases'])


@pytest.mark.parametrize('kind', ['empty', 'low_relevance', 'invalid_output', 'invalid_citation'])
def test_no_answer_reason_is_distinct(query_context, trace_log, kind):
    ctx = query_context
    if kind == 'empty':
        ctx.store.search.return_value = ()
    elif kind == 'low_relevance':
        ctx.reranker.rerank.return_value = (0.0, 0.0)
    elif kind == 'invalid_output':
        ctx.llm.structured_completion.return_value = {'untrusted': 'private result'}
    else:
        ctx.llm.structured_completion.return_value['citation_chunk_ids'] = [999]
    ctx.query()
    e = event(trace_log)
    assert e['reason'] == {'empty': 'no_visible_evidence', 'low_relevance': 'below_relevance',
                           'invalid_output': 'invalid_generation', 'invalid_citation': 'invalid_citation'}[kind]


def test_unknown_failure_is_reraised_and_not_dumped(query_context, trace_log):
    query_context.embedder.embed.side_effect = RuntimeError('private infrastructure detail')
    with pytest.raises(RuntimeError):
        query_context.query()
    e = event(trace_log)
    assert e['status'] == 'error' and e['error_code'] == 'SYS.INTERNAL_ERROR'
    assert 'private infrastructure detail' not in str(e)


def test_auth_revocation_is_not_relabelled_as_provider_failure(query_context, trace_log):
    def revoke(**kwargs):
        raise AppError(code=IamErrorCode.FORBIDDEN, message='private denial detail', status_code=403)
    query_context.store.search.side_effect = revoke
    with pytest.raises(AppError):
        query_context.query()
    e = event(trace_log)
    assert e['status'] == 'rejected' and e['error_code'] == IamErrorCode.FORBIDDEN
    assert 'private denial detail' not in str(e)


def test_request_traces_do_not_accumulate(query_context, trace_log):
    query_context.query()
    first = event(trace_log)
    trace_log.reset_mock()
    query_context.store.search.return_value = ()
    query_context.query()
    second = event(trace_log)
    assert len(first['phases']) == 4 and len(second['phases']) == 2
    assert second['selected_count'] == 0


def test_trace_sink_failure_does_not_change_business_result(query_context, trace_log):
    trace_log.info.side_effect = RuntimeError('sink failure')
    assert query_context.query().status == 'answered'


def test_invalid_rerank_has_validation_reason(query_context, trace_log):
    query_context.reranker.rerank.return_value = (float('nan'), 0.8)
    assert query_context.query().status == 'answered'
    e = event(trace_log)
    assert e['rerank_fallback_error_code'] == 'AI.INVALID_OUTPUT'
    assert e['phases'][2]['status'] == 'invalid_output'


def test_generation_timeout_is_not_schema_invalidity(query_context, trace_log):
    query_context.llm.structured_completion.side_effect = ProviderTimeoutError('private')
    query_context.query()
    assert event(trace_log)['reason'] == 'generation_unavailable'


def test_model_metadata_is_bounded_and_not_arbitrary_text(query_context, trace_log):
    query_context.llm.model_version = 'https://private.invalid?api_key=private-secret\nprivate content'
    query_context.query()
    assert event(trace_log)['phases'][-1]['model_version'] == 'unknown'


@pytest.mark.parametrize('stage', ['embed', 'rerank', 'before_generation', 'before_response'])
def test_warning_sink_failure_preserves_safe_business_result(context, trace_log, stage):
    c = context
    trace_log.warning.side_effect = RuntimeError('sink failure')
    if stage == 'embed':
        c.embedder.embed.side_effect = ProviderTimeoutError('private')
    elif stage == 'rerank':
        c.reranker.rerank.side_effect = ProviderTimeoutError('private')
    else:
        method = c.reranker.rerank if stage == 'before_generation' else c.llm.structured_completion
        result = method.return_value
        def withdraw(*args, **kwargs):
            c.docs[31].status = 'ARCHIVED'
            return result
        method.side_effect = withdraw
    expected = 'answered' if stage == 'rerank' else 'knowledge_unavailable'
    assert c.query().status == expected
    assert event(trace_log)['status'] == expected
    assert not c.session.in_transaction()


@pytest.mark.parametrize('stage,expected,count', [
    ('initial', 'initial_access', 0), ('store', 'evidence_access', 2),
    ('reranker', 'pre_generation_access', 3), ('llm', 'pre_response_access', 4),
])
def test_actual_read_phase_revocation_has_trace(context, trace_log, stage, expected, count):
    from app.domain.iam import Principal
    c = context
    def revoke():
        c.state['actor'] = Principal(5, 'ACTIVE', 'synthetic', ())
    if stage == 'initial':
        revoke()
    else:
        method = {'store': c.store.search, 'reranker': c.reranker.rerank,
                  'llm': c.llm.structured_completion}[stage]
        result = method.return_value
        def mutate(*args, **kwargs):
            revoke()
            return result
        method.side_effect = mutate
    with pytest.raises(AppError) as error:
        c.query()
    assert error.value.status_code == 403
    e = event(trace_log)
    assert e['status'] == 'rejected' and e['stage'] == expected
    assert len(e['phases']) == count
    assert not c.session.in_transaction()
