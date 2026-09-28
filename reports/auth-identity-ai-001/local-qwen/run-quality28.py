"""Bounded real 28-case diagnostics; existing provider pipeline, no business IO."""
from pathlib import Path
from datetime import UTC, datetime
import argparse
import hashlib
import json
import math
import os
import re
import runpy
import site
import subprocess
import sys
import threading
import time

ROOT=Path(r'D:\property-ai');OUT=Path(__file__).parent
SCREEN_IDS=('GOLD-005','GOLD-006','GOLD-008','GOLD-012','GOLD-018','GOLD-021','PHOTO-002','PHOTO-004')
NINE_B_DEADLINE_UTC=datetime.fromisoformat('2026-09-29T14:36:10+00:00')
HISTORICAL_NINE_B_TOTAL_CALL_LIMIT=64
DELIVERY_V11_TOTAL_CALL_LIMIT=80
NINE_B_TOTAL_MODEL_SECONDS=7200
NINE_B_START_GPU_FREE_MIB=8200
NINE_B_RESIDENT_REQUEST_GPU_FREE_MIB=2500
COLLECTOR_PATH='reports/auth-identity-ai-001/local-qwen/run-quality28.py'
NINE_B_RESUME_BINDING=ROOT/'reports/real-ai-integration-027/qwen35-9b-resume-binding.json'
NINE_B_COLLECTOR_RECOVERY_BINDING=ROOT/'reports/real-ai-integration-027/qwen35-9b-collector-recovery-binding.json'
MODEL_INPUT_CONTEXT={
    'house':{'property_name':'合成测试小区','building_name':'合成楼','unit_name':'合成单元','room_no':'测试房'},
    'reporter_relationships':['OWNER'],
}
MODEL_INPUT_CONTRACT={
    'version':'quality28-model-input-v2-bounded-images',
    'context_static':MODEL_INPUT_CONTEXT,
    'history_fields':['category','status','summary'],
    'text_case_field':'report','photo_case_field':'paired_report',
    'asr_text':None,'vision_text':'provider-result-or-empty',
    'image':{'mime_type':'image/jpeg','kind':'IMAGE','max_dimension':1024,'transform':'proportional-no-crop'},
    'snapshot_ids':{'work_order_id':0,'tenant_id':701,'property_id':702,'house_id':703,
                    'building_id':704,'analysis_id':0,'analysis_version':1,'work_order_version':1},
    'input_hash':'sha256(report utf-8)',
    'controls':{'context':4096,'temperature':0,'thinking':False,'llm_max_tokens':512,
                'vlm_max_tokens':256,'timeout_seconds':60,'parallel':1},
}
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
import httpx
from app.core.config import Settings
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION,WorkOrderAnalysisOutput,map_analysis_output
from app.domain.observed_event import build_evidence_catalog
from app.provider.factory import build_llm_provider
from app.provider.vision import QwenVLMProvider
from app.service.analysis import AnalysisService,AnalysisSnapshot,MediaRef

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def budget_state_from_events(events):
    """Treat a transport failure without a response as remotely UNKNOWN.

    A local HTTP exception is not proof that Ollama stopped evaluating the
    request.  The append-only ledger therefore blocks the next reservation
    until every reservation has a terminal response-backed completion.
    """
    reserved=[row for row in events if row['event']=='RESERVED']
    completed={row['request_id']:row for row in events if row['event']=='COMPLETED'}
    unknown=[]
    for row in reserved:
        completion=completed.get(row['request_id'])
        if completion is None or completion.get('remote_completion')!='RESPONSE_RECEIVED':
            unknown.append(row['request_id'])
    return {
        'attempted':len(reserved),
        'model_seconds':sum(float(row['model_seconds']) for row in completed.values()),
        'unknown':unknown,
    }
def protocol_source_hashes_match(left,right):
    """Collector-only resume changes must not relax model/protocol source binding."""
    left=dict(left);right=dict(right)
    left.pop(COLLECTOR_PATH,None);right.pop(COLLECTOR_PATH,None)
    return left==right
def model_input_contract_sha256():
    encoded=json.dumps(MODEL_INPUT_CONTRACT,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(encoded).hexdigest()
def incomplete_screen_resume_items(all_items,screen):
    if (screen.get('status')!='INCOMPLETE' or screen.get('stop_reason')!='RESOURCE_FLOOR_CROSSED'
            or screen.get('candidate')!=1 or screen.get('phase')!='screen'):
        raise AssertionError('Only the recorded candidate-01 resource-floor screen may resume')
    completed=[row['id'] for row in screen.get('results',[]) if row.get('provider_status')=='SUCCESS']
    if completed!=['GOLD-005'] or screen.get('attempted_requests')!=1:
        raise AssertionError('Recorded screen boundary changed; never skip or replay unknown cases')
    selected=[item for item in all_items if item['id'] in SCREEN_IDS and item['id'] not in completed]
    if [item['id'] for item in selected] != list(SCREEN_IDS[1:]):
        raise AssertionError('Screen continuation order changed')
    return selected
def incomplete_collector_recovery_items(all_items,continuation):
    completed_ids=list(SCREEN_IDS[1:-1])
    if (continuation.get('status')!='INCOMPLETE' or continuation.get('stop_reason')!='KeyError'
            or continuation.get('candidate')!=1 or continuation.get('phase')!='screen-continuation'
            or continuation.get('attempted_requests')!=7
            or [row.get('id') for row in continuation.get('results',[])]!=completed_ids
            or not all(row.get('provider_status')=='SUCCESS' for row in continuation.get('results',[]))):
        raise AssertionError('Only the recorded collector-only screen failure may recover')
    selected=[item for item in all_items if item['id']==SCREEN_IDS[-1]]
    if [item['id'] for item in selected]!=[SCREEN_IDS[-1]]:
        raise AssertionError('Collector recovery must run only the unattempted final photo')
    return selected
def validate_incomplete_screen_binding(screen,current,canonical_first,binding,current_collector_sha):
    controls={key:current['controls'][key] for key in (
        'context','temperature','thinking','llm_max_tokens','vlm_max_tokens','timeout_seconds','parallel'
    )}
    prior_controls={key:screen['controls'][key] for key in controls}
    if (
        binding.get('decision')!='APPROVED_COLLECTOR_RESUME_ONLY'
        or binding.get('prior_collector_sha256')!=screen['source_hashes'].get(COLLECTOR_PATH)
        or binding.get('current_collector_sha256')!=current_collector_sha
        or binding.get('model_input_contract_sha256')!=model_input_contract_sha256()
        or binding.get('frozen_controls')!=controls
        or prior_controls!=controls
        or screen.get('model')!=current.get('model')
        or screen.get('model_digest')!=current.get('model_digest')
        or screen.get('prompt_version')!=current.get('prompt_version')
        or screen.get('vision_prompt_version')!=current.get('vision_prompt_version')
        or screen.get('gold_hashes')!=current.get('gold_hashes')
        or not protocol_source_hashes_match(screen.get('source_hashes',{}),current.get('source_hashes',{}))
        or screen['results'][0].get('input')!=canonical_first
        or binding.get('preserved_first_request_body_sha256')!=screen['results'][0]['stages'][0]['request']['body_sha256']
    ):
        raise AssertionError('9B screen continuation binding drifted before reservation')
    return True
def validate_collector_recovery_binding(continuation,current,binding,current_collector_sha,continuation_path):
    frozen_controls={key:current['controls'][key] for key in (
        'context','temperature','thinking','llm_max_tokens','vlm_max_tokens','timeout_seconds','parallel'
    )}
    if (
        binding.get('decision')!='APPROVED_COLLECTOR_FAILURE_RECOVERY_ONLY'
        or binding.get('continuation_sha256')!=sha(continuation_path)
        or binding.get('prior_collector_sha256')!=continuation.get('source_hashes',{}).get(COLLECTOR_PATH)
        or binding.get('current_collector_sha256')!=current_collector_sha
        or binding.get('frozen_controls')!=frozen_controls
        or continuation.get('model')!=current.get('model')
        or continuation.get('model_digest')!=current.get('model_digest')
        or continuation.get('prompt_version')!=current.get('prompt_version')
        or continuation.get('vision_prompt_version')!=current.get('vision_prompt_version')
        or continuation.get('gold_hashes')!=current.get('gold_hashes')
        or not protocol_source_hashes_match(continuation.get('source_hashes',{}),current.get('source_hashes',{}))
    ):
        raise AssertionError('9B collector recovery binding drifted before reservation')
    incomplete_collector_recovery_items(
        [{'id':case_id} for case_id in SCREEN_IDS],continuation
    )
    return True
def diagnostic_mechanical_checks(service,payload,sources,accepted,*,is_photo,evaluation_policy,threshold):
    """Evaluate the canonical observed-event contract through production code."""
    evidence_catalog=build_evidence_catalog(
        report_text=sources.get('report'),asr_text=sources.get('asr'),vision_text=sources.get('vision')
    )
    mapped,error=service._validate_payload(payload,evidence_catalog=evidence_catalog)
    if mapped is None:
        return {'schema_and_mapping':False,'error_code':error}
    return {
        'schema_and_mapping':True,
        'has_cjk_hint':bool(re.search('[\u4e00-\u9fff]',str(payload['summary']))),
        'summary':payload['summary'],'severity':payload['severity'],'priority':payload['priority'],
        'risk':payload['risk'],'required_role':payload['required_role'],
        'required_skill':payload['required_skill'],'confidence':payload['confidence'],
        'below_confidence_threshold':float(payload['confidence'])<threshold,
        'business_fallback':mapped.low_confidence,
        'category_skill_risk':(None if is_photo else any(
            payload['category']==category and payload['required_skill']==skill and payload['risk'] in risks
            for category,skill,risks in accepted
        )),
        'event_contract':mapped.event_trace['contract'] if mapped.event_trace else None,
        'effective_event_key':mapped.subcategory,
        'evaluation_policy':evaluation_policy,
    }
def raw_json_checks(raw,kind):
    try:
        response=json.loads(raw);content=response['choices'][0]['message']['content'];value=json.loads(content)
        checks={'http_body_json':True,'content_strict_json_object':isinstance(value,dict)}
        if not isinstance(value,dict):return checks
        if kind=='VLM':
            confidence=value.get('confidence')
            checks.update(description_type=isinstance(value.get('description'),str) and bool(value['description'].strip()),
                labels_type=isinstance(value.get('labels'),list) and all(isinstance(s,str) for s in value['labels']),
                confidence_valid=isinstance(confidence,(int,float)) and not isinstance(confidence,bool) and math.isfinite(confidence) and 0<=confidence<=1)
        return checks
    except (ValueError,TypeError,KeyError,IndexError):return {'content_strict_json_object':False}

class PhotoStorage:
    def __init__(self,images):self.images=images
    def get_object(self,key):return self.images[key]

def main():
    if '--supplement' in sys.argv:
        sys.argv.remove('--supplement')
        runpy.run_path(str(OUT/'run-approved-supplement.py'), run_name='__main__')
        return
    parser=argparse.ArgumentParser();parser.add_argument('--round',type=int,choices=range(4));parser.add_argument('--model',choices=('qwen3.5:4b','qwen3.5:9b'),default='qwen3.5:4b');parser.add_argument('--phase',choices=('screen','fixed','novel'));parser.add_argument('--candidate',type=int,choices=(1,2,3));parser.add_argument('--resume-incomplete-screen',action='store_true');parser.add_argument('--resume-screen-collector-failure',action='store_true');args=parser.parse_args()
    if args.model=='qwen3.5:4b':
        assert args.round is not None and args.phase is None and args.candidate is None,'Historical 4B mode requires --round only'
        target=OUT/f'quality28-round{args.round}.json';run_out=OUT;max_requests=32
    else:
        assert args.round is None and args.phase is not None and args.candidate is not None,'9B mode requires --phase and --candidate'
        assert args.candidate==1 or args.phase in {'fixed','novel'},'Later candidates cannot add another screen after the bounded repair'
        assert args.candidate!=3 or args.phase in {'fixed','novel'},'Delivery v11 candidate uses one complete fixed run and one novel run'
        assert not (args.resume_incomplete_screen and args.resume_screen_collector_failure),'Only one bounded screen recovery is allowed'
        assert not (args.resume_incomplete_screen or args.resume_screen_collector_failure) or (args.candidate==1 and args.phase=='screen'),'Only candidate-01 screen can resume'
        budget_root=(ROOT/'reports/real-ai-integration-027/diagnostic-r3-9b-v11'
                     if args.candidate==3 else ROOT/'reports/real-ai-integration-027/diagnostic-r3-9b')
        active_call_limit=(DELIVERY_V11_TOTAL_CALL_LIMIT
                           if args.candidate==3 else HISTORICAL_NINE_B_TOTAL_CALL_LIMIT)
        run_out=budget_root/f'candidate-{args.candidate:02d}';run_out.mkdir(parents=True,exist_ok=True)
        phase_label=('screen-continuation-recovery' if args.resume_screen_collector_failure else
                     'screen-continuation' if args.resume_incomplete_screen else args.phase)
        target=run_out/f'{phase_label}.json';max_requests=2 if args.resume_screen_collector_failure else 9 if args.resume_incomplete_screen else 10 if args.phase=='screen' else 4 if args.phase=='novel' else 22 if args.candidate==1 else 32
    assert not target.exists(),'Recorded round is immutable: inspect or explicit new repair round; no replay'
    helpers=runpy.run_path(str(OUT/'probe-model.py'));api=helpers['api'];sample=helpers['resource_sample']
    textpath=OUT/'gold-set-24.json';photopath=OUT/'repair-image-gold.json'
    assert sha(textpath)=='c3c93b419b7d5dea12b529152cde661c16826cc708b1d80d5b26d39d70ddfc1f'
    assert sha(photopath)=='df10abfde5a0144c480e29041ddedcc146a0182a6452dacd2ed97498c88cfdbe'
    gold=json.loads(textpath.read_text(encoding='utf-8'));photos=json.loads(photopath.read_text(encoding='utf-8'))
    assert len(gold['cases'])==24 and len(photos['cases'])==4
    overlay_path=ROOT/'reports/real-ai-integration-027/qwen35-9b-evaluation-overlay.json'
    overlay=(json.loads(overlay_path.read_text(encoding='utf-8')) if args.model=='qwen3.5:9b' else None)
    novel_path=ROOT/'reports/real-ai-integration-027/qwen35-9b-novel-set.json'
    novel=(json.loads(novel_path.read_text(encoding='utf-8')) if args.model=='qwen3.5:9b' else None)
    if overlay is not None:
        assert overlay['base_gold_unchanged'] is True and set(overlay['cases'])=={item['id'] for item in gold['cases']}
    all_items=gold['cases']+photos['cases']
    if args.model=='qwen3.5:9b' and args.phase=='screen':
        if args.resume_screen_collector_failure:
            selected_items=incomplete_collector_recovery_items(
                all_items,json.loads((run_out/'screen-continuation.json').read_text(encoding='utf-8'))
            )
        elif args.resume_incomplete_screen:
            selected_items=incomplete_screen_resume_items(all_items,json.loads((run_out/'screen.json').read_text(encoding='utf-8')))
        else:
            selected_items=[item for item in all_items if item['id'] in SCREEN_IDS]
            assert tuple(item['id'] for item in selected_items)==SCREEN_IDS
    elif args.model=='qwen3.5:9b' and args.phase=='novel':
        assert novel['frozen_before_first_9b_inference'] is True and len(novel['cases'])==4
        selected_items=novel['cases']
    else:selected_items=all_items
    downloads=json.loads((OUT/'repair-image-downloads.json').read_text(encoding='utf-8'))
    bounded_manifest_path=ROOT/'reports/real-ai-integration-027/qwen35-9b-bounded-images.json'
    bounded_manifest=(json.loads(bounded_manifest_path.read_text(encoding='utf-8'))
                      if args.model=='qwen3.5:9b' and args.candidate>=2 else None)
    images={}
    for p in photos['cases']:
        original_path=OUT/'repair-images'/p['file'];row=next(r for r in downloads if Path(r['file']).name==p['file'])
        assert sha(original_path)==row['sha256'] and p['gps_metadata_present'] is False
        path=original_path
        if bounded_manifest is not None:
            image_record=next(r for r in bounded_manifest['files'] if r['file']==p['file'])
            assert image_record['original_sha256']==sha(original_path)
            assert max(image_record['output_width'],image_record['output_height'])<=bounded_manifest['maximum_dimension']
            path=OUT/'repair-images-1024'/p['file']
            assert sha(path)==image_record['output_sha256']
        images[p['file']]=path.read_bytes()
    digest=(json.loads((OUT/'download-4b-result.json').read_text(encoding='utf-8'))['manifest_sha256']
            if args.model=='qwen3.5:4b' else '6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7')
    expected_version='0.34.2' if args.model=='qwen3.5:4b' else '0.34.3'
    assert api('/api/version')['version']==expected_version
    assert next(r for r in api('/api/tags')['models'] if r['name']==args.model)['digest']==digest
    def resident():
        rows=api('/api/ps')['models']
        assert len(rows)<=1 and all(r['name']==args.model and r['digest']==digest and r['context_length']==4096 for r in rows),'Resident model/context mismatch'
        return rows
    runtime_path=(ROOT/'reports/real-ai-integration-027/qwen35-9b-runtime-resume.json')
    runtime=(json.loads((OUT/'diagnostic-runtime28.json').read_text(encoding='utf-8-sig')) if args.model=='qwen3.5:4b' else
             json.loads((runtime_path if runtime_path.exists() else ROOT/'reports/real-ai-integration-027/qwen35-9b-runtime.json').read_text(encoding='utf-8-sig')))
    assert runtime['no_cloud'] and runtime['context_length']==4096 and runtime['concurrency']==1
    budget_path=(budget_root/'qwen-budget.jsonl') if args.model=='qwen3.5:9b' else run_out/'qwen-budget.jsonl'
    def budget_events():
        if not budget_path.exists():return []
        return [json.loads(line) for line in budget_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    def budget_state():
        return budget_state_from_events(budget_events())
    def append_budget(row):
        payload=(json.dumps({'at_utc':datetime.now(UTC).isoformat(),**row},ensure_ascii=False,separators=(',',':'))+'\n').encode()
        fd=os.open(budget_path,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:os.write(fd,payload);os.fsync(fd)
        finally:os.close(fd)
    if args.model=='qwen3.5:9b':
        state=budget_state();assert not state['unknown'],f'Prior request completion UNKNOWN: {state["unknown"]}'
        assert state['attempted']<active_call_limit and state['model_seconds']<NINE_B_TOTAL_MODEL_SECONDS
        assert datetime.now(UTC)<NINE_B_DEADLINE_UTC,'48-hour delivery window expired'
    e={'task_id':'REAL-AI-INTEGRATION-027' if args.model.endswith(':9b') else 'SELFHOST-AI-LOCAL-001','round':args.round,'phase':phase_label if args.model.endswith(':9b') else args.phase,'candidate':args.candidate,'repair_rounds_used':args.round,'base_sha':'f5c8154',
       'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'at_utc':datetime.now(UTC).isoformat(),
       'model':args.model,'model_digest':digest,'quantization':'Q4_K_M','prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,
       'vision_prompt_version':getattr(QwenVLMProvider,'prompt_version','unversioned-original'),
       'source_hashes':{p:sha(ROOT/p) for p in ['backend/app/service/analysis.py','backend/app/domain/ai.py','backend/app/domain/observed_event.py','backend/app/provider/llm/__init__.py','backend/app/provider/vision/__init__.py','backend/app/provider/http.py','reports/auth-identity-ai-001/local-qwen/run-quality28.py']},
       'gold_hashes':{textpath.name:sha(textpath),photopath.name:sha(photopath),**({'qwen35-9b-evaluation-overlay.json':sha(overlay_path),'qwen35-9b-novel-set.json':sha(novel_path)} if overlay is not None else {}),**({'qwen35-9b-bounded-images.json':sha(bounded_manifest_path)} if bounded_manifest is not None else {})},'runtime':runtime,
       'image_input':({'mode':'proportional-jpeg-max-1024','manifest':str(bounded_manifest_path),'manifest_sha256':sha(bounded_manifest_path)} if bounded_manifest is not None else {'mode':'original-gold-files'}),
       'controls':{'context':4096,'temperature':0,'thinking':False,'llm_max_tokens':512,'vlm_max_tokens':256,'timeout_seconds':60,'parallel':1,'max_cases':1 if args.resume_screen_collector_failure else 7 if args.resume_incomplete_screen else 8 if args.phase=='screen' else 4 if args.phase=='novel' else 28,'max_inference_requests':max_requests},
       'cumulative_budget':{'path':str(budget_path),'call_limit':active_call_limit,'model_seconds_limit':NINE_B_TOTAL_MODEL_SECONDS,'deadline_utc':NINE_B_DEADLINE_UTC.isoformat()} if args.model=='qwen3.5:9b' else None,
       'business_calls':False,'database_access':False,'queue_access':False,'business_runtime_changed':False,'attempted_requests':0,'reused_requests':0,'results':[],'status':'RUNNING'}
    if args.resume_incomplete_screen:
        screen=json.loads((run_out/'screen.json').read_text(encoding='utf-8'))
        binding=json.loads(NINE_B_RESUME_BINDING.read_text(encoding='utf-8'))
        canonical_first=next(item for item in all_items if item['id']=='GOLD-005')
        validate_incomplete_screen_binding(screen,e,canonical_first,binding,sha(Path(__file__)))
        e['resume_binding']={'path':str(NINE_B_RESUME_BINDING),'sha256':sha(NINE_B_RESUME_BINDING)}
    if args.resume_screen_collector_failure:
        continuation_path=run_out/'screen-continuation.json'
        continuation=json.loads(continuation_path.read_text(encoding='utf-8'))
        recovery_binding=json.loads(NINE_B_COLLECTOR_RECOVERY_BINDING.read_text(encoding='utf-8'))
        validate_collector_recovery_binding(
            continuation,e,recovery_binding,sha(Path(__file__)),continuation_path
        )
        e['resume_binding']={'path':str(NINE_B_COLLECTOR_RECOVERY_BINDING),'sha256':sha(NINE_B_COLLECTOR_RECOVERY_BINDING)}
    def save():target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    current={}
    def request_hook(request):
        assert str(request.url)=='http://127.0.0.1:11435/v1/chat/completions' and request.method=='POST'
        body=json.loads(request.content);assert body['model']==args.model and body['temperature']==0 and body['reasoning_effort']=='none'
        assert body['max_tokens']==(256 if current['kind']=='VLM' else 512)
        assert e['attempted_requests']<max_requests
        if args.model=='qwen3.5:9b':
            state=budget_state();assert not state['unknown'],'Prior request completion UNKNOWN'
            assert state['attempted']<active_call_limit and state['model_seconds']<NINE_B_TOTAL_MODEL_SECONDS
            assert datetime.now(UTC)<NINE_B_DEADLINE_UTC,'48-hour delivery window expired'
            request_id=f'candidate-{args.candidate:02d}-{e["phase"]}-{e["attempted_requests"]+1:03d}'
            append_budget({'event':'RESERVED','request_id':request_id,'candidate':args.candidate,'phase':e['phase'],'kind':current['kind'],'body_sha256':hashlib.sha256(request.content).hexdigest()})
            current['budget_request_id']=request_id
        e['attempted_requests']+=1;current['attempted']=True
        current['request']={'url':str(request.url),'method':request.method,'body':body,'body_sha256':hashlib.sha256(request.content).hexdigest()}
        save()
    def response_hook(response):
        response.read();current['http_status']=response.status_code;current['raw_response_body']=response.text
        current['raw_response_sha256']=hashlib.sha256(response.content).hexdigest()
        current['json_checks']=raw_json_checks(response.text,current['kind'])
        try:current['usage']=response.json().get('usage')
        except (ValueError,AttributeError):current['usage']=None
        save()
    client=httpx.Client(trust_env=False,timeout=60,event_hooks={'request':[request_hook],'response':[response_hook]})
    settings=Settings(_env_file=None,app_env='test',llm_provider='production',llm_base_url='http://127.0.0.1:11435/v1',llm_model=args.model,llm_api_key='local-loopback-no-auth',llm_disable_thinking=True,llm_max_output_tokens=512,llm_timeout_seconds=60)
    service=AnalysisService.__new__(AnalysisService);service._settings=settings;service._llm=build_llm_provider(settings);service._llm._client=client;service._clock=lambda:datetime.now(UTC)
    service._vlm=QwenVLMProvider(base_url='http://127.0.0.1:11435/v1',model=args.model,api_key='local-loopback-no-auth',timeout_seconds=60,storage=PhotoStorage(images),client=client,disable_thinking=True,max_output_tokens=256)
    logs=[];service._write_call_log=lambda *a,**kw:logs.append(kw)
    def stage(kind,call,case):
        nonlocal current
        loaded=resident();before=sample()
        required_gpu=(NINE_B_RESIDENT_REQUEST_GPU_FREE_MIB if loaded else
                      NINE_B_START_GPU_FREE_MIB if args.model=='qwen3.5:9b' else 6000)
        if before['ram_free_mib']<4500 or before['gpu_free_mib']<required_gpu:
            e['stop_reason']='RESOURCE_HEADROOM';e['guard_sample']=before;save();raise RuntimeError('RESOURCE_HEADROOM')
        current={'kind':kind,'at_utc':datetime.now(UTC).isoformat(),'before':before,'resident_before':loaded,'samples':[],'attempted':False,'status':'STARTING'}
        case['stages'].append(current);save();stop=threading.Event();started=time.perf_counter();logs.clear()
        def monitor():
            while not stop.wait(1):
                try:current['samples'].append(sample())
                except Exception as exc:current['samples'].append({'error_type':type(exc).__name__})
        monitor_thread=threading.Thread(target=monitor,daemon=True);monitor_thread.start()
        try:
            answer=call();current['provider_result']=answer;current['status']='RECORDED'
            return answer
        except Exception as exc:
            current['status']='EXCEPTION';current['error_type']=type(exc).__name__
            raise
        finally:
            stop.set();monitor_thread.join(timeout=10);current['latency_seconds']=round(time.perf_counter()-started,3)
            current['call_log']=list(logs);current['after']=sample();current['resident_after']=resident()
            measured=[before,current['after']]+[s for s in current['samples'] if 'ram_free_mib' in s]
            current['min_ram_free_mib']=min(s['ram_free_mib'] for s in measured);current['min_gpu_free_mib']=min(s['gpu_free_mib'] for s in measured)
            if args.model=='qwen3.5:9b' and current.get('budget_request_id'):
                append_budget({'event':'COMPLETED','request_id':current['budget_request_id'],'candidate':args.candidate,'phase':e['phase'],'kind':kind,'status':current['status'],'model_seconds':current['latency_seconds'],'raw_response_sha256':current.get('raw_response_sha256'),'remote_completion':'RESPONSE_RECEIVED' if current.get('raw_response_sha256') else 'UNKNOWN'})
            if current['min_ram_free_mib']<2000 or current['min_gpu_free_mib']<2000:e['stop_reason']='RESOURCE_FLOOR_CROSSED'
            save()
    try:
        save()
        reused={}
        reused_sources={}
        if args.model=='qwen3.5:9b' and args.phase=='fixed' and args.candidate==1:
            screen_path=run_out/'screen.json';assert screen_path.exists(),'Complete identical screen phase is required before fixed phase'
            screen=json.loads(screen_path.read_text(encoding='utf-8'))
            continuation_path=run_out/'screen-continuation.json'
            if screen['status']=='INCOMPLETE':
                assert continuation_path.exists(),'Immutable incomplete screen requires its explicit continuation'
                continuation=json.loads(continuation_path.read_text(encoding='utf-8'))
                binding=json.loads(NINE_B_RESUME_BINDING.read_text(encoding='utf-8'))
                canonical_first=next(item for item in all_items if item['id']=='GOLD-005')
                validate_incomplete_screen_binding(
                    screen,e,canonical_first,binding,binding['current_collector_sha256']
                )
                assert continuation['model']==args.model and continuation['model_digest']==digest
                assert continuation['prompt_version']==e['prompt_version'] and continuation['vision_prompt_version']==e['vision_prompt_version']
                assert continuation['gold_hashes']==e['gold_hashes']
                assert continuation['resume_binding']['sha256']==sha(NINE_B_RESUME_BINDING)
                assert continuation['source_hashes'][COLLECTOR_PATH]==binding['current_collector_sha256']
                assert all(continuation['controls'][key]==e['controls'][key] for key in binding['frozen_controls'])
                recovery_path=run_out/'screen-continuation-recovery.json'
                if continuation['status']=='INCOMPLETE':
                    assert recovery_path.exists(),'Collector-only failure requires its immutable bounded recovery'
                    recovery=json.loads(recovery_path.read_text(encoding='utf-8'))
                    recovery_binding=json.loads(NINE_B_COLLECTOR_RECOVERY_BINDING.read_text(encoding='utf-8'))
                    validate_collector_recovery_binding(
                        continuation,e,recovery_binding,sha(Path(__file__)),continuation_path
                    )
                    assert recovery['status']=='RECORDED_NEEDS_SEMANTIC_REVIEW' and recovery['phase']=='screen-continuation-recovery'
                    assert recovery['resume_binding']['sha256']==sha(NINE_B_COLLECTOR_RECOVERY_BINDING)
                    assert recovery['source_hashes'][COLLECTOR_PATH]==sha(Path(__file__))
                    assert recovery['model']==args.model and recovery['model_digest']==digest
                    assert recovery['prompt_version']==e['prompt_version'] and recovery['vision_prompt_version']==e['vision_prompt_version']
                    assert recovery['gold_hashes']==e['gold_hashes']
                    assert all(recovery['controls'][key]==e['controls'][key] for key in recovery_binding['frozen_controls'])
                    screen_rows=screen['results']+continuation['results']+recovery['results']
                    attempted=screen['attempted_requests']+continuation['attempted_requests']+recovery['attempted_requests']
                    sources=(screen['source_hashes'],continuation['source_hashes'],recovery['source_hashes'])
                    reused_sources.update({row['id']:recovery_path for row in recovery['results']})
                else:
                    assert continuation['status']=='RECORDED_NEEDS_SEMANTIC_REVIEW' and continuation['phase']=='screen-continuation'
                    screen_rows=screen['results']+continuation['results']
                    attempted=screen['attempted_requests']+continuation['attempted_requests']
                    sources=(screen['source_hashes'],continuation['source_hashes'])
                reused_sources.update({row['id']:continuation_path for row in continuation['results']})
            else:
                assert screen['status']=='RECORDED_NEEDS_SEMANTIC_REVIEW'
                screen_rows=screen['results'];attempted=screen['attempted_requests'];sources=(screen['source_hashes'],)
            reused_sources.update({row['id']:screen_path for row in screen['results']})
            assert all(protocol_source_hashes_match(source,e['source_hashes']) for source in sources)
            assert screen['model']==args.model and screen['model_digest']==digest
            assert screen['prompt_version']==e['prompt_version'] and screen['vision_prompt_version']==e['vision_prompt_version']
            assert screen['gold_hashes']==e['gold_hashes']
            reused={row['id']:row for row in screen_rows};assert tuple(reused)==SCREEN_IDS and attempted==10
        if args.model=='qwen3.5:9b' and args.phase=='novel':
            fixed=json.loads((run_out/'fixed.json').read_text(encoding='utf-8'))
            assert fixed['status']=='RECORDED_NEEDS_SEMANTIC_REVIEW' and fixed['source_hashes']==e['source_hashes'] and fixed['gold_hashes']==e['gold_hashes']
        for item in selected_items:
            if item['id'] in reused:
                case=json.loads(json.dumps(reused[item['id']]))
                source_path=reused_sources[item['id']]
                case['reused_from']={'path':str(source_path),'source_sha256':sha(source_path),'protocol_source_hashes_verified':True,
                    'frozen_controls_and_canonical_input_verified':True,
                    'resume_binding_sha256':sha(NINE_B_RESUME_BINDING) if NINE_B_RESUME_BINDING.exists() else None}
                e['results'].append(case);e['reused_requests']+=2 if item['id'].startswith('PHOTO') else 1;save();continue
            is_photo=item['id'].startswith('PHOTO');report=item['paired_report'] if is_photo else item['report']
            case={'id':item['id'],'input':item,'stages':[],'semantic_review':'PENDING_INDIVIDUAL_REVIEW_NOT_AUTOMATIC_PASS'};e['results'].append(case)
            context=json.loads(json.dumps(MODEL_INPUT_CONTEXT,ensure_ascii=False))
            context['recent_house_orders']=[dict(zip(MODEL_INPUT_CONTRACT['history_fields'],r)) for r in item.get('history',[])]
            media=(MediaRef(storage_key=item['file'],mime_type='image/jpeg',kind='IMAGE'),) if is_photo else ()
            snapshot=AnalysisSnapshot(work_order_id=0,tenant_id=701,property_id=702,house_id=703,building_id=704,analysis_id=0,analysis_version=1,work_order_version=1,input_hash=hashlib.sha256(report.encode()).hexdigest(),text=report,existing_asr_text=None,images=media,audio=(),context=context)
            vision=None
            if is_photo:
                case['image_sha256']=hashlib.sha256(images[item['file']]).hexdigest()
                vision,error=stage('VLM',lambda:service._run_vlm(snapshot,'quality28-'+item['id'],1),case)
                case['vision_error']=error
                if e.get('stop_reason'):break
            payload,error,status=stage('LLM',lambda:service._run_llm(snapshot,asr_text=None,vision_text=vision,request_id='quality28-'+item['id'],attempt=1),case)
            case.update(raw_payload=payload,provider_error=error,provider_status=status)
            accepted=[] if is_photo else item['accept'] if overlay is None or args.phase=='novel' else overlay['cases'][item['id']]['accept']
            case['mechanical_checks']=diagnostic_mechanical_checks(
                service,payload,{'report':report,'asr':'','vision':vision or ''},accepted,
                is_photo=is_photo,evaluation_policy='original-fixed-gold' if overlay is None else overlay['version'],
                threshold=gold['common_gold']['confidence_threshold'],
            )
            save();print(json.dumps({'case':item['id'],'requests':e['attempted_requests'],'payload':payload},ensure_ascii=False),flush=True)
            if e.get('stop_reason'):break
        expected_cases=1 if args.resume_screen_collector_failure else 7 if args.resume_incomplete_screen else 8 if args.model=='qwen3.5:9b' and args.phase=='screen' else 4 if args.model=='qwen3.5:9b' and args.phase=='novel' else 28
        expected_calls=2 if args.resume_screen_collector_failure else 9 if args.resume_incomplete_screen else 10 if args.model=='qwen3.5:9b' and args.phase=='screen' else 4 if args.model=='qwen3.5:9b' and args.phase=='novel' else 32
        e['status']='RECORDED_NEEDS_SEMANTIC_REVIEW' if len(e['results'])==expected_cases and e['attempted_requests']+e['reused_requests']==expected_calls and not e.get('stop_reason') else 'INCOMPLETE'
    except Exception as exc:e['status']='INCOMPLETE';e.setdefault('stop_reason',type(exc).__name__);raise
    finally:
        client.close();e['finished_at_utc']=datetime.now(UTC).isoformat();save()
        print(json.dumps({'status':e['status'],'cases':len(e['results']),'requests':e['attempted_requests'],'stop_reason':e.get('stop_reason')}),flush=True)

if __name__=='__main__':main()
