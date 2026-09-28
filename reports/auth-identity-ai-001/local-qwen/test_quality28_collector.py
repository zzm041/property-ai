"""Collector-only checks; no inference, business requests or fixtures."""
import json
import subprocess
from pathlib import Path
import runpy
import pytest
helpers=runpy.run_path(str(Path(__file__).with_name('run-quality28.py')))
check=helpers['raw_json_checks']
def body(value):return json.dumps({'choices':[{'message':{'content':json.dumps(value)}}]})
def test_non_json_response_preserved_by_safe_check():
    assert check('<html>502</html>','LLM')=={'content_strict_json_object':False}
def test_scalar_and_fenced_json_not_strict_objects():
    assert check(body([]),'LLM')['content_strict_json_object'] is False
    assert check(json.dumps({'choices':[{'message':{'content':'```json\n{}\n```'}}]}),'LLM')['content_strict_json_object'] is False
def test_raw_vision_types_and_confidence_not_coerced_to_pass():
    good={'description':'插座','labels':['electrical'],'confidence':0.8}
    assert all(check(body(good),'VLM').values())
    for confidence in (float('nan'),1.2,True,'0.8'):
        assert check(body({**good,'confidence':confidence}),'VLM')['confidence_valid'] is False
    assert check(body({**good,'labels':'electrical'}),'VLM')['labels_type'] is False
def test_low_confidence_and_rule_fallback_are_separate():
    output=helpers['WorkOrderAnalysisOutput'].model_validate({'category':'other','subcategory':'unknown','severity':'low','priority':'p4','summary':'尚不清楚','risk':'none','required_role':'STAFF','required_skill':'other','confidence':0.99})
    assert helpers['map_analysis_output'](output).low_confidence
    assert not output.confidence<0.70
def test_existing_media_reference_contract():
    media=helpers['MediaRef'](storage_key='normal-socket.jpg',mime_type='image/jpeg',kind='IMAGE')
    assert media.kind=='IMAGE'

def test_budget_blocks_transport_failure_without_remote_response():
    state=helpers['budget_state_from_events']([
        {'event':'RESERVED','request_id':'request-1'},
        {'event':'COMPLETED','request_id':'request-1','status':'EXCEPTION','model_seconds':60,
         'remote_completion':'UNKNOWN'},
    ])
    assert state=={'attempted':1,'model_seconds':60.0,'unknown':['request-1']}

def test_budget_accepts_response_backed_provider_rejection():
    state=helpers['budget_state_from_events']([
        {'event':'RESERVED','request_id':'request-1'},
        {'event':'COMPLETED','request_id':'request-1','status':'EXCEPTION','model_seconds':3.5,
         'remote_completion':'RESPONSE_RECEIVED'},
    ])
    assert state=={'attempted':1,'model_seconds':3.5,'unknown':[]}

def test_runtime_guard_deadline_and_observation_fail_closed():
    guard=helpers['runtime_guard_reason']
    assert guard(now=10,deadline=10,observed=None,observation_error=RuntimeError('sample'),ram_floor=4500)==\
        'REQUEST_WALL_OR_CUMULATIVE_DEADLINE'
    assert guard(now=9,deadline=10,observed=None,observation_error=RuntimeError('sample'),ram_floor=4500)==\
        'RESOURCE_OBSERVATION_FAILED'
    assert guard(now=9,deadline=10,observed={'ram_free_mib':4499,'gpu_free_mib':9000},
                 observation_error=None,ram_floor=4500)=='RESOURCE_FLOOR_CROSSED'
    assert guard(now=9,deadline=10,observed={'ram_free_mib':9000,'gpu_free_mib':1999},
                 observation_error=None,ram_floor=4500)=='RESOURCE_FLOOR_CROSSED'
    assert guard(now=9,deadline=10,observed={'ram_free_mib':9000,'gpu_free_mib':2000},
                 observation_error=None,ram_floor=4500) is None

class _FakeSocket:
    def __init__(self,code):self.code=code
    def __enter__(self):return self
    def __exit__(self,*_args):return False
    def settimeout(self,_timeout):pass
    def connect_ex(self,address):
        assert address==('127.0.0.1',11435)
        return self.code

@pytest.mark.parametrize(('code','expected'),[(10061,True),(111,True),(0,False)])
def test_loopback_listener_closed_requires_explicit_refusal(monkeypatch,code,expected):
    monkeypatch.setattr(helpers['socket'],'socket',lambda *_args,**_kwargs:_FakeSocket(code))
    assert helpers['loopback_listener_closed']() is expected

def test_loopback_listener_closed_rejects_ambiguous_status(monkeypatch):
    monkeypatch.setattr(helpers['socket'],'socket',lambda *_args,**_kwargs:_FakeSocket(10060))
    with pytest.raises(RuntimeError,match='LISTENER_STATUS_UNKNOWN_10060'):
        helpers['loopback_listener_closed']()

def test_terminate_bound_runtime_confirms_exact_tree_and_listener(monkeypatch):
    runtime={'pid':40700,'executable':r'C:\\ollama.exe','launched_at_utc':'2026-09-27T14:46:24Z'}
    identity={'pid':40700,'path':runtime['executable'],'started_utc':runtime['launched_at_utc']}
    calls={'identity':0}
    def identity_then_gone(_runtime):
        calls['identity']+=1
        if calls['identity']==1:return identity
        raise subprocess.CalledProcessError(1,['powershell'])
    runtime_globals=helpers['terminate_bound_runtime'].__globals__
    monkeypatch.setitem(runtime_globals,'bound_runtime_identity',identity_then_gone)
    def exact_taskkill(args,**kwargs):
        assert args==['taskkill','/PID','40700','/T','/F']
        assert kwargs=={'capture_output':True,'text':True,'timeout':15}
        return type('R',(),{'returncode':0})()
    monkeypatch.setattr(helpers['subprocess'],'run',exact_taskkill)
    monkeypatch.setitem(runtime_globals,'loopback_listener_closed',lambda:True)
    result=helpers['terminate_bound_runtime'](runtime)
    assert result['verified_before_stop']==identity
    assert result['stopped']=={'pid':40700,'tree_exit_confirmed':True,'listener_exit_confirmed':True}

def test_mechanical_check_uses_observed_event_contract_not_legacy_subcategory():
    payload={'category':'cleaning','observations':[{'label':'杂物清扫','subject':'楼道地面',
        'evidence_id':'report:1'}],'severity':'low','priority':'p3',
        'summary':'楼道角落纸盒需要清扫。','risk':'none','required_role':'STAFF',
        'required_skill':'cleaning','confidence':1.0}
    service=helpers['AnalysisService'].__new__(helpers['AnalysisService'])
    service._settings=helpers['Settings'](_env_file=None,app_env='test',llm_provider='production')
    result=helpers['diagnostic_mechanical_checks'](
        service,payload,{'report':'楼道角落散落纸盒，需要清扫。','asr':'','vision':''},
        [['cleaning','cleaning',['none']]],is_photo=False,
        evaluation_policy='test',threshold=0.70,
    )
    assert result['schema_and_mapping'] is True
    assert result['event_contract']=='observed-event-v2-source-id'
    assert result['effective_event_key'].startswith('evt2:')
    assert result['category_skill_risk'] is True

def test_incomplete_9b_screen_resume_preserves_first_case_and_order():
    items=[{'id':case_id} for case_id in helpers['SCREEN_IDS']]
    screen={'status':'INCOMPLETE','stop_reason':'RESOURCE_FLOOR_CROSSED','candidate':1,
        'phase':'screen','attempted_requests':1,
        'results':[{'id':'GOLD-005','provider_status':'SUCCESS'}]}
    selected=helpers['incomplete_screen_resume_items'](items,screen)
    assert [item['id'] for item in selected]==list(helpers['SCREEN_IDS'][1:])

def test_collector_failure_recovery_runs_only_unattempted_last_photo():
    items=[{'id':case_id} for case_id in helpers['SCREEN_IDS']]
    continuation={'status':'INCOMPLETE','stop_reason':'KeyError','candidate':1,
        'phase':'screen-continuation','attempted_requests':7,
        'results':[{'id':case_id,'provider_status':'SUCCESS'} for case_id in helpers['SCREEN_IDS'][1:-1]]}
    selected=helpers['incomplete_collector_recovery_items'](items,continuation)
    assert selected==[{'id':'PHOTO-004'}]

def test_photo_mechanical_check_does_not_require_text_accept_matrix():
    source=Path(helpers['__file__']).read_text(encoding='utf-8')
    assert "accepted=[] if is_photo else" in source
    assert "phase_label=('screen-continuation-recovery'" in source

def test_screen_resume_source_compatibility_ignores_only_collector():
    collector=helpers['COLLECTOR_PATH']
    prior={collector:'old','backend/app/service/analysis.py':'same','backend/app/domain/ai.py':'same'}
    current={collector:'new','backend/app/service/analysis.py':'same','backend/app/domain/ai.py':'same'}
    assert helpers['protocol_source_hashes_match'](prior,current)
    current['backend/app/domain/ai.py']='changed'
    assert not helpers['protocol_source_hashes_match'](prior,current)

def test_candidate_source_binding_includes_observed_event_contract():
    source=Path(helpers['__file__']).read_text(encoding='utf-8')
    assert "'backend/app/domain/observed_event.py'" in source
    collector=helpers['COLLECTOR_PATH']
    prior={collector:'same','backend/app/domain/observed_event.py':'old'}
    current={collector:'same','backend/app/domain/observed_event.py':'new'}
    assert not helpers['protocol_source_hashes_match'](prior,current)

def test_9b_resume_retains_approved_resource_guards():
    assert helpers['NINE_B_START_GPU_FREE_MIB']==8200
    assert helpers['NINE_B_RESIDENT_REQUEST_GPU_FREE_MIB']>=2000
    assert helpers['HISTORICAL_NINE_B_TOTAL_CALL_LIMIT']==64
    assert helpers['DELIVERY_V11_TOTAL_CALL_LIMIT']==80
    assert helpers['NINE_B_TOTAL_MODEL_SECONDS']==7200

def test_screen_resume_binding_rejects_protocol_or_control_drift_before_requests():
    collector=helpers['COLLECTOR_PATH']
    canonical={'id':'GOLD-005','report':'纸盒'}
    controls={'context':4096,'temperature':0,'thinking':False,'llm_max_tokens':512,
        'vlm_max_tokens':256,'timeout_seconds':60,'parallel':1}
    screen={'model':'qwen3.5:9b','model_digest':'digest','prompt_version':'prompt',
        'vision_prompt_version':'vision','controls':controls,'gold_hashes':{'gold':'same'},
        'source_hashes':{collector:'prior','backend/app/service/analysis.py':'same'},
        'results':[{'input':canonical,'stages':[{'request':{'body_sha256':'body'}}]}]}
    current={'model':'qwen3.5:9b','model_digest':'digest','prompt_version':'prompt',
        'vision_prompt_version':'vision','controls':controls,'gold_hashes':{'gold':'same'},
        'source_hashes':{collector:'current','backend/app/service/analysis.py':'same'}}
    binding={'decision':'APPROVED_COLLECTOR_RESUME_ONLY','prior_collector_sha256':'prior',
        'current_collector_sha256':'current','frozen_controls':controls,
        'preserved_first_request_body_sha256':'body',
        'model_input_contract_sha256':helpers['model_input_contract_sha256']()}
    assert helpers['validate_incomplete_screen_binding'](screen,current,canonical,binding,'current')
    current['source_hashes']['backend/app/service/analysis.py']='drift'
    import pytest
    with pytest.raises(AssertionError,match='before reservation'):
        helpers['validate_incomplete_screen_binding'](screen,current,canonical,binding,'current')

def test_binding_validation_runs_before_client_and_any_request_reservation():
    source=Path(helpers['__file__']).read_text(encoding='utf-8')
    validation=source.index('validate_incomplete_screen_binding(screen,e,canonical_first,binding')
    client=source.index('client=httpx.Client')
    request_hook=source.index('def request_hook(request)')
    assert validation < request_hook < client
    assert source.count('validate_incomplete_screen_binding(')>=2
    assert "continuation['resume_binding']['sha256']==sha(NINE_B_RESUME_BINDING)" in source
    assert "continuation['source_hashes'][COLLECTOR_PATH]==binding['current_collector_sha256']" in source
    recovery_validation=source.index('validate_collector_recovery_binding(\n            continuation,e,recovery_binding')
    assert recovery_validation < request_hook < client

def test_model_input_contract_is_explicit_and_stable():
    contract=helpers['MODEL_INPUT_CONTRACT']
    assert contract['version']=='quality28-model-input-v2-bounded-images'
    assert contract['image']=={'mime_type':'image/jpeg','kind':'IMAGE','max_dimension':1024,'transform':'proportional-no-crop'}
    assert contract['controls']=={'context':4096,'temperature':0,'thinking':False,
        'llm_max_tokens':512,'vlm_max_tokens':256,'timeout_seconds':60,'parallel':1}
    assert len(helpers['model_input_contract_sha256']())==64
