"""No live inference, DB or service: bounded HTTP/ledger and vector contracts."""
import importlib.util
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
from app.provider.vector import MilvusKnowledgeStore
from app.service.retrieval import RetrievalService

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('local_ai_http_027',ROOT/'scripts/ops/local_ai_http.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

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

def test_only_rag_prompt_can_reach_qwen_in_current_package():
    value={'model':'qwen3.5:4b','messages':[{'role':'user','content':'依据资料回答'}],
        'temperature':0,'reasoning_effort':'none','max_tokens':512,'response_format':{'type':'json_object'}}
    raw=json.dumps(value).encode()
    with pytest.raises(ValueError):m.decode('/v1/chat/completions','application/json',raw,'wo-analysis-v10-observed-event')
    assert m.decode('/v1/chat/completions','application/json',raw,'rag-answer-v2')[0]=='qwen'

def runner(tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps({'private':str(tmp_path/'run'),'host':'127.0.0.1','port':11436}))
    return m.Runner(path)


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


@pytest.mark.parametrize('kind,cleanup_fails,expected',[
    ('embedding',False,'FAILED'),('embedding',True,'UNKNOWN'),('qwen',False,'UNKNOWN')])
def test_resource_stop_confirms_job_or_persists_unknown(tmp_path,monkeypatch,kind,cleanup_fails,expected):
    r=runner(tmp_path);r.config['bge_python']='dummy-not-executed';started=[False];terminated=[]
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
    with pytest.raises(RuntimeError,match='RESOURCE_FLOOR'):r.run(kind,['synthetic'])
    saved=json.loads((r.private/'call-001/result.json').read_text(encoding='utf-8'))
    assert saved['status']==expected and terminated==[True]
    assert saved['owned_exit_confirmed'] is (not cleanup_fails)
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
