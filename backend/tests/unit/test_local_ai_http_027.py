"""No live inference, DB or service: bounded HTTP/ledger and vector contracts."""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import io
import wave
import httpx
import pytest
from app.core.config import Settings
from app.provider.errors import ProviderInvalidOutputError,ProviderUnavailableError
from app.provider.llm import QwenLLMProvider
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.observed_event import work_order_analysis_response_format
from app.domain.vision import VISION_ANALYSIS_PROMPT_VERSION, vision_analysis_response_format
from app.provider.vector import MilvusKnowledgeStore
from app.service.retrieval import RetrievalService

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('local_ai_http_027',ROOT/'scripts/ops/local_ai_http.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def _load_adapter_candidate(monkeypatch, model):
    monkeypatch.setenv('PROPERTY_AI_LOCAL_QWEN_MODEL',model)
    candidate_spec=importlib.util.spec_from_file_location(
        f'local_ai_http_{model.replace(":", "_")}',ROOT/'scripts/ops/local_ai_http.py'
    )
    candidate=importlib.util.module_from_spec(candidate_spec)
    candidate_spec.loader.exec_module(candidate)
    return candidate


def test_only_explicit_reviewed_qwen_candidates_can_configure_adapter(monkeypatch):
    candidate=_load_adapter_candidate(monkeypatch,'qwen3.5:9b')
    assert candidate.MODELS['qwen']=='qwen3.5:9b'
    assert candidate.DIGEST=='6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
    assert candidate.QWEN_RUNTIME_VERSION=='0.34.3'
    current=_load_adapter_candidate(monkeypatch,'qwen38-27b-q3km-g55:7af3b8f')
    assert current.DIGEST=='eb18a122286f7a67d36dc8129f9caa1c0f7bb8449d7669a79e3ecb3d81289e0c'
    with pytest.raises(RuntimeError,match='UNAPPROVED_QWEN_CANDIDATE'):
        _load_adapter_candidate(monkeypatch,'qwen3.5:latest')

@pytest.mark.parametrize('path,obj',[
    ('/v1/embeddings',{'model':'other','input':['a']}),
    ('/v1/embeddings',{'model':'bge-m3','input':['a']*17}),
    ('/v1/embeddings',{'model':'bge-m3','input':['a'*4001]}),
    ('/v1/embeddings',{'model':'bge-m3','input':['a'],'path':'D:/private'}),
    ('/v1/rerank',{'model':'bge-reranker-v2-m3','query':'a','documents':[]}),
    ('/v1/chat/completions',{'model':'qwen3.5:4b'}),
    ('/load',{'model':'bge-m3','url':'https://example.com'}),
])
def test_strict_http_limits_and_no_arbitrary_model_path_url(path,obj):
    with pytest.raises(ValueError):m.decode(path,'application/json',json.dumps(obj).encode(),'')

def test_audio_is_bytes_and_bounded_pcm_not_url():
    with pytest.raises(ValueError):m.validate('asr',b'http://example.com/audio')
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0'*320)
    m.validate('asr',b.getvalue())
    with pytest.raises(ValueError):m.validate('asr','D:/private.wav')

def test_only_reviewed_rag_or_exact_work_order_schema_can_reach_qwen():
    value={'model':'qwen3.5:4b','messages':[{'role':'user','content':'依据资料回答'}],
        'temperature':0,'reasoning_effort':'none','max_tokens':512,'response_format':{'type':'json_object'}}
    raw=json.dumps(value).encode()
    with pytest.raises(ValueError):m.decode('/v1/chat/completions','application/json',raw,'unknown-prompt')
    assert m.decode('/v1/chat/completions','application/json',raw,'rag-answer-v2')[0]=='qwen'
    value['response_format']=work_order_analysis_response_format()
    raw=json.dumps(value,ensure_ascii=False).encode()
    assert m.decode('/v1/chat/completions','application/json',raw,WORK_ORDER_ANALYSIS_PROMPT_VERSION)[0]=='qwen'
    value['response_format']['json_schema']['schema']['properties']['category']['enum'].append('noise')
    with pytest.raises(ValueError,match='QWEN_SCHEMA_MISMATCH'):
        m.decode('/v1/chat/completions','application/json',json.dumps(value,ensure_ascii=False).encode(),WORK_ORDER_ANALYSIS_PROMPT_VERSION)


def test_adapter_schema_is_generated_from_domain_contract_at_runtime():
    assert m.WORK_ORDER_RESPONSE_FORMAT==work_order_analysis_response_format()


def test_exact_vision_schema_and_multimodal_shape_are_guarded_without_wrapper_rewrite():
    value={
        'model':'qwen3.5:4b',
        'messages':[{'role':'user','content':[
            {'type':'text','text':'仅描述合成维修图片中的可见现象'},
            {'type':'image_url','image_url':{'url':'data:image/png;base64,cG5n'}},
        ]}],
        'temperature':0,
        'reasoning_effort':'none',
        'max_tokens':256,
        'response_format':vision_analysis_response_format(),
    }
    raw=json.dumps(value,ensure_ascii=False).encode()
    assert m.decode('/v1/chat/completions','application/json',raw,VISION_ANALYSIS_PROMPT_VERSION)[0]=='qwen'
    value['response_format']={'type':'json_object'}
    with pytest.raises(ValueError,match='QWEN_SCHEMA_MISMATCH'):
        m.decode('/v1/chat/completions','application/json',json.dumps(value,ensure_ascii=False).encode(),VISION_ANALYSIS_PROMPT_VERSION)

def runner(tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps({'private':str(tmp_path/'run'),'host':'127.0.0.1','port':11436}))
    return m.Runner(path)


def test_rag_only_scope_rejects_work_order_protocol_but_allows_rag():
    work_order = {
        'model': 'qwen3.5:4b',
        'messages': [{'role': 'user', 'content': '合成报修'}],
        'response_format': m.WORK_ORDER_RESPONSE_FORMAT,
        'temperature': 0,
        'reasoning_effort': 'none',
        'max_tokens': 512,
    }
    raw = json.dumps(work_order, ensure_ascii=False).encode()
    with pytest.raises(ValueError, match='RAG_ONLY_NO_AUTO_AUTHORIZATION'):
        m.decode('/v1/chat/completions', 'application/json', raw, m.WORK_ORDER_PROMPT_VERSION, 'rag_only')

    rag = {**work_order, 'response_format': {'type': 'json_object'}}
    assert m.decode(
        '/v1/chat/completions',
        'application/json',
        json.dumps(rag, ensure_ascii=False).encode(),
        'rag-answer-v2',
        'rag_only',
    )[0] == 'qwen'


def test_work_order_scope_rejects_rag_and_vector_endpoints():
    rag = {
        'model': m.MODELS['qwen'], 'messages': [{'role': 'user', 'content': '资料'}],
        'response_format': {'type': 'json_object'}, 'temperature': 0,
        'reasoning_effort': 'none', 'max_tokens': 512,
    }
    with pytest.raises(ValueError, match='WORK_ORDER_ONLY_NO_RAG_AUTHORIZATION'):
        m.decode('/v1/chat/completions','application/json',json.dumps(rag).encode(),'rag-answer-v2','work_order_only')
    with pytest.raises(ValueError, match='WORK_ORDER_ONLY_ENDPOINT'):
        m.decode('/v1/embeddings','application/json',json.dumps({'model':'bge-m3','input':['a']}).encode(),'','work_order_only')


def test_scoped_runtime_limits_and_duration_are_enforced(tmp_path, monkeypatch):
    config = {
        'private': str(tmp_path / 'run'),
        'host': '127.0.0.1',
        'port': 11436,
        'scope': 'rag_only',
        'limits': {'asr': 1, 'embedding': 1, 'reranker': 1, 'qwen': 2},
        'duration_seconds': 7200,
    }
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    scoped = m.Runner(path)
    monkeypatch.setattr(m.time, 'time', lambda: 1000.0)
    folder, deadline = scoped.reserve('embedding', 'one')
    assert deadline == 8200.0
    assert json.loads((folder / 'reservation.json').read_text())['max_calls'] == 1
    m.save(folder / 'result.json', {'status': 'SUCCESS'})
    with pytest.raises(RuntimeError, match='CALL_LIMIT'):
        scoped.reserve('embedding', 'two')


def test_scoped_runtime_checks_configured_window_before_reservation(tmp_path, monkeypatch):
    config = {
        'private': str(tmp_path / 'run'), 'host': '127.0.0.1', 'port': 11436,
        'limits': {'asr': 1, 'embedding': 2, 'reranker': 2, 'qwen': 2},
        'duration_seconds': 7200,
    }
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    scoped = m.Runner(path)
    clock = [100.0]
    monkeypatch.setattr(m.time, 'time', lambda: clock[0])
    first, _ = scoped.reserve('embedding', 'one')
    m.save(first / 'result.json', {'status': 'SUCCESS'})
    clock[0] = 2000.0
    second, deadline = scoped.reserve('reranker', 'two')
    assert deadline == 7300.0
    m.save(second / 'result.json', {'status': 'SUCCESS'})
    clock[0] = 7300.0
    with pytest.raises(RuntimeError, match='WINDOW_EXHAUSTED'):
        scoped.reserve('qwen', 'three')
    assert len(list(scoped.private.glob('call-*'))) == 2


def test_drain_atomically_blocks_new_admission_without_disturbing_active_request(tmp_path):
    scoped = runner(tmp_path)
    assert scoped.acquire_request() is True
    assert scoped.begin_drain() is True
    scoped.lock.release()
    assert scoped.acquire_request() is False
    assert scoped.admission() == {'accepting_requests': False, 'request_active': False}


@pytest.mark.parametrize('bad', [
    {'scope': 'work_order_only'},
    {'limits': {'asr': 1, 'embedding': 12, 'reranker': 8, 'qwen': 19}},
    {'duration_seconds': 7201},
])
def test_scoped_runtime_rejects_widened_configuration(tmp_path, bad):
    config = {'private': str(tmp_path / 'run'), 'host': '127.0.0.1', 'port': 11436, **bad}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    with pytest.raises(ValueError):
        m.Runner(path)


def test_work_order_scope_requires_reviewed_runtime_binding(tmp_path):
    config={'private':str(tmp_path/'run'),'host':'127.0.0.1','port':11437,
        'scope':'work_order_only','evidence_root':str(tmp_path)}
    path=tmp_path/'config.json';path.write_text(json.dumps(config),encoding='utf-8')
    with pytest.raises(ValueError,match='WORK_ORDER_RUNTIME_BINDING_REQUIRED'):
        m.Runner(path)


def continuation_runner(tmp_path,valid_review=True):
    private=tmp_path/'run';evidence=tmp_path/'evidence';private.mkdir();evidence.mkdir()
    input_hash='same-synthetic-audio'
    for i in range(1,30):
        folder=private/f'call-{i:03d}';folder.mkdir()
        m.save(folder/'reservation.json',{'kind':'asr' if i==22 else 'embedding','unix':float(i),'input_hash':input_hash if i==22 else f'input-{i}'})
        m.save(folder/'result.json',{'status':'FAILED','reason':'RESOURCE_FLOOR'} if i==22 else {'status':'SUCCESS'})
    reservation=private/'call-022/reservation.json';result=private/'call-022/result.json'
    resolution=evidence/'resolution.json'
    m.save(resolution,{'record':'call-022','original':{'status':'FAILED','historical_immediate_exit_confirmed':'UNKNOWN'},
        'current_execution':{'currently_executing':False},'explicit_same_input_replay':{'authorized':True}})
    resolution_hash=hashlib.sha256(resolution.read_bytes()).hexdigest();review=evidence/'review.json'
    m.save(review,{'verdict':'PASS_SCOPED_CALL_RESOLUTION' if valid_review else 'NEEDS_CHANGES','resolution_sha256':resolution_hash})
    config={'private':str(private),'host':'127.0.0.1','port':11436,'evidence_root':str(evidence),
        'continuation':{'task_id':'REAL-AI-INTEGRATION-027','run_id':'ai027-b439223fda','base_count':29,'duration_seconds':900,
            'limits':{'embedding':6,'reranker':6,'qwen':6,'asr':1},'asr_start_ram_mib':11264,'resolutions':[{
                'record':'call-022','reservation_path':str(reservation),'reservation_sha256':hashlib.sha256(reservation.read_bytes()).hexdigest(),
                'result_sha256':hashlib.sha256(result.read_bytes()).hexdigest(),'resolution_path':str(resolution),'resolution_sha256':resolution_hash,
                'review_path':str(review),'review_sha256':hashlib.sha256(review.read_bytes()).hexdigest()}]}}
    path=tmp_path/'config.json';path.write_text(json.dumps(config),encoding='utf-8');return m.Runner(path),input_hash


def test_reviewed_resolution_starts_one_continuation_window_without_new_ledger(tmp_path,monkeypatch):
    r,input_hash=continuation_runner(tmp_path);monkeypatch.setattr(m.time,'time',lambda:1000.)
    assert r.gate()=='READY'
    folder,deadline=r.reserve('asr',input_hash);record=json.loads((folder/'reservation.json').read_text())
    assert folder.name=='call-030' and deadline==1900
    assert record['window']=='r2-continuation-01' and record['window_start_unix']==1000
    assert record['replay_of']=='call-022' and record['incremental_max_calls']==1
    m.save(folder/'result.json',{'status':'FAILED','owned_exit_confirmed':True})
    with pytest.raises(RuntimeError,match='FAILED_INPUT'):
        r.reserve('asr',input_hash)


def test_resolution_without_independent_pass_remains_blocked(tmp_path):
    r,_=continuation_runner(tmp_path,valid_review=False)
    assert r.gate()=='BLOCKED_RESOLUTION_INVALID'
    with pytest.raises(RuntimeError,match='COMPLETION_UNKNOWN'):r.reserve('embedding','different')


@pytest.mark.parametrize('drifted',[{'status':'SUCCESS'},{'status':'FAILED','owned_exit_confirmed':True}])
def test_reviewed_resolution_blocks_original_result_hash_drift(tmp_path,drifted):
    r,_=continuation_runner(tmp_path)
    (r.private/'call-022/result.json').write_text(json.dumps(drifted),encoding='utf-8')
    assert r.gate()=='BLOCKED_RESOLUTION_INVALID'
    with pytest.raises(RuntimeError,match='COMPLETION_UNKNOWN'):r.reserve('embedding','different')


def test_cpu_guard_waits_without_call_and_keeps_threshold(tmp_path,monkeypatch):
    r=runner(tmp_path);samples=iter([7600,8191,8192]);sleeps=[]
    monkeypatch.setattr(m,'ram',lambda:next(samples))
    monkeypatch.setattr(m.time,'sleep',lambda seconds:sleeps.append(seconds))
    def reserved(kind,input_hash):raise RuntimeError('REACHED_RESERVATION')
    monkeypatch.setattr(r,'reserve',reserved)
    with pytest.raises(RuntimeError,match='REACHED_RESERVATION'):
        r.run('embedding',['测试'])
    assert sleeps==[.25,.25]
    assert not list(r.private.glob('call-*'))


def test_cpu_guard_times_out_without_reserving_or_loading(tmp_path,monkeypatch):
    r=runner(tmp_path);clock=iter([0,36])
    monkeypatch.setattr(m,'ram',lambda:8191)
    monkeypatch.setattr(m.time,'monotonic',lambda:next(clock))
    with pytest.raises(RuntimeError,match='CPU_START_RAM_GUARD'):
        r.run('embedding',['测试'])
    assert not list(r.private.glob('call-*'))

def test_append_only_budget_survives_restart_and_reserves_before_load(tmp_path):
    r=runner(tmp_path);folder,_=r.reserve('asr')
    with pytest.raises(RuntimeError,match='COMPLETION_UNKNOWN'):m.Runner(r.path).reserve('embedding')
    m.save(folder/'result.json',{'status':'FAILED','owned_exit_confirmed':True})
    with pytest.raises(RuntimeError,match='CALL_LIMIT'):m.Runner(r.path).reserve('asr')

def test_total_window_is_not_reset_by_another_model(tmp_path,monkeypatch):
    r=runner(tmp_path);monkeypatch.setattr(m.time,'time',lambda:100.)
    folder,_=r.reserve('embedding');m.save(folder/'result.json',{'status':'SUCCESS'})
    monkeypatch.setattr(m.time,'time',lambda:1900.)
    with pytest.raises(RuntimeError,match='WINDOW_EXHAUSTED'):m.Runner(r.path).reserve('reranker')


@pytest.mark.parametrize('record',[{'status':'UNKNOWN','owned_exit_confirmed':False}, {'status':'FAILED'}])
def test_unknown_or_legacy_unproved_failure_blocks_all_new_inputs(tmp_path,record):
    r=runner(tmp_path);folder,_=r.reserve('asr','first');m.save(folder/'result.json',record)
    with pytest.raises(RuntimeError,match='COMPLETION_UNKNOWN'):
        m.Runner(r.path).reserve('embedding','different-input')
    assert len(list(r.private.glob('call-*')))==1


@pytest.mark.parametrize('kind,cleanup_fails,remote_stops,expected',[
    ('embedding',False,False,'FAILED'),('embedding',True,False,'UNKNOWN'),
    ('qwen',False,False,'UNKNOWN'),('qwen',False,True,'FAILED')])
def test_resource_stop_confirms_job_or_persists_unknown(tmp_path,monkeypatch,kind,cleanup_fails,remote_stops,expected):
    r=runner(tmp_path);r.config['bge_python']='dummy-not-executed';started=[False];terminated=[]
    if kind=='qwen':
        r.runtime_binding={'pid':42,'executable':'fixed','launched_at_utc':'fixed'}
    class Job:
        name='unit-request-job'
        def active(self):return 1
        def terminate_and_confirm(self):
            terminated.append(True)
            if cleanup_fails:raise RuntimeError('unknown cleanup')
        def close(self):pass
    class Proc:
        pid=123;returncode=None
        def __init__(self,*a,**kw):started[0]=True;kw['stdout'].write(b'{"phase":"load"}\n');kw['stdout'].flush()
        def poll(self):return None
        def wait(self,timeout):self.returncode=1;return 1
    monkeypatch.setattr(m,'RequestJob',Job);monkeypatch.setattr(m.subprocess,'Popen',Proc)
    monkeypatch.setattr(m,'qwen_guard',lambda:None);monkeypatch.setattr(m,'validate',lambda *a:None)
    monkeypatch.setattr(m,'ram',lambda:1000 if started[0] else 10000)
    monkeypatch.setattr(m,'gpu',lambda:7000);monkeypatch.setattr(m.time,'sleep',lambda n:None)
    def stop_runtime(*args,**kwargs):
        if not remote_stops:raise RuntimeError('stop failed')
        return {'stopped':{'tree_exit_confirmed':True,'listener_exit_confirmed':True}}
    monkeypatch.setattr(m,'terminate_bound_runtime',stop_runtime)
    with pytest.raises(RuntimeError,match='RESOURCE_FLOOR'):r.run(kind,['synthetic'])
    saved=json.loads((r.private/'call-001/result.json').read_text(encoding='utf-8'))
    assert saved['status']==expected and terminated==[True]
    assert saved['owned_exit_confirmed'] is (not cleanup_fails)
    if kind=='qwen':
        assert saved['remote_completion']==('CONTROLLED_STOP' if remote_stops else 'UNKNOWN')
    if expected=='UNKNOWN':
        with pytest.raises(RuntimeError,match='COMPLETION_UNKNOWN'):m.Runner(r.path).reserve('reranker','different')

def test_real_provider_marks_prompt_and_rejects_truncation():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"status":"answered"}'}}]})
    p=QwenLLMProvider(base_url='http://127.0.0.1:11436/v1',model='qwen3.5:4b',api_key='unit',timeout_seconds=1,client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderInvalidOutputError):p.structured_completion(prompt='test',prompt_version='rag-answer-v2')
    assert seen[0].headers['X-Prompt-Version']=='rag-answer-v2'

def test_rag_prompt_states_real_output_contract_not_fake_provider_assumption():
    prompt=RetrievalService._prompt('领取时间？',(SimpleNamespace(id=7,source_locator='规则#1',excerpt='领取时间为下午三点。'),))
    assert all(s in prompt for s in ('status, answer, citation_chunk_ids','simplified Chinese','chunk-id:7','下午三点'))

@pytest.mark.parametrize('drift',['dim','metric','revision'])
def test_existing_collection_mismatch_refuses_without_mutation(drift):
    s=Settings(_env_file=None,embedding_revision='fixed-revision',embedding_normalization='l2',milvus_metric_type='COSINE')
    store=MilvusKnowledgeStore(s)
    c=SimpleNamespace(schema=SimpleNamespace(fields=[SimpleNamespace(name='vector',params={'dim':1024})],description=store._model_metadata()),indexes=[SimpleNamespace(field_name='vector',params={'metric_type':'COSINE'})])
    store._validate_collection(c,1024)
    if drift=='dim':c.schema.fields[0].params['dim']=8
    elif drift=='metric':c.indexes[0].params['metric_type']='IP'
    else:c.schema.description='development'
    with pytest.raises(ProviderUnavailableError):store._validate_collection(c,1024)
