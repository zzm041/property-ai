"""Bounded real 28-case diagnostics; existing provider pipeline, no business IO."""
from pathlib import Path
from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
import math
import os
import re
import runpy
import site
import socket
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
TWENTY_SEVEN_B_MODEL='qwen3.5:27b-q4_K_M'
TWENTY_SEVEN_B_TOTAL_CALL_LIMIT=120
TWENTY_SEVEN_B_REQUEST_TIMEOUT_SECONDS=180
TWENTY_SEVEN_B_RESIDENT_REQUEST_GPU_FREE_MIB=2000
TWENTY_SEVEN_B_ADMISSION_OVERHEAD_MIB=1536
TWENTY_SEVEN_B_TERMINATION_MARGIN_SECONDS=15
TWENTY_SEVEN_B_CANDIDATE_SOURCE=ROOT/'reports/real-ai-integration-027/qwen35-27b-candidate-source.json'
TWENTY_SEVEN_B_RUNTIME=ROOT/'reports/real-ai-integration-027/qwen35-27b-runtime.json'
QWEN38_MODEL='qwen38-27b-q3km-g55:7af3b8f'
QWEN38_CANDIDATE_SOURCE=ROOT/'reports/real-ai-integration-027/qwen38-27b-q3km-g55-binding.json'
QWEN38_RUNTIME=ROOT/'reports/real-ai-integration-027/qwen38-27b-q3km-g55-runtime.json'
QWEN38_UNKNOWN_RESOLUTION_BINDING=ROOT/'reports/real-ai-integration-027/qwen38-unknown-resolution-binding.json'
QWEN38_LEDGER_NORMALIZATION=ROOT/'reports/real-ai-integration-027/qwen38-ledger-normalization.json'
QWEN38_SCREEN_IDS=('GOLD-007','GOLD-008','GOLD-012','GOLD-018','GOLD-022','GOLD-024','PHOTO-003','PHOTO-004')
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
def sha_matches(value,path):
    return isinstance(value,str) and re.fullmatch('[0-9a-fA-F]{64}',value) is not None and value.lower()==sha(path)
def canonical_rows_sha256(rows):
    encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(encoded).hexdigest()
def budget_state_from_events(events, reviewed_resolutions=frozenset()):
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
        if (completion is None or completion.get('remote_completion') not in {
            'RESPONSE_RECEIVED','CONTROLLED_RUNTIME_TERMINATION_CONFIRMED'
        }) and row['request_id'] not in reviewed_resolutions:
            unknown.append(row['request_id'])
    return {
        'attempted':len(reserved),
        'model_seconds':sum(float(row['model_seconds']) for row in completed.values())+sum(
            float(row['model_seconds']) for row in events if row.get('event')=='MODEL_SECONDS_CARRIED_FORWARD'
        ),
        'unknown':unknown,
    }
def reviewed_resolution_ids(events,binding_path,ledger_path):
    """Allow only an independently reviewed, exact historical UNKNOWN exit.

    The original completion stays UNKNOWN and consumes its request/time budget.
    This binding only proves that the old execution is no longer running so a
    different frozen candidate can reserve a later request.  A new UNKNOWN is
    absent from the exact binding and therefore still blocks.
    """
    if not binding_path.exists():return frozenset()
    binding=json.loads(binding_path.read_text(encoding='utf-8'))
    allowed=(ROOT/'reports').resolve()
    resolution=(ROOT/binding['resolution_path']).resolve()
    review=(ROOT/binding['review_path']).resolve()
    if not resolution.is_relative_to(allowed) or not review.is_relative_to(allowed):
        raise RuntimeError('UNKNOWN_RESOLUTION_PATH_SCOPE')
    if not sha_matches(binding.get('resolution_sha256'),resolution):
        raise RuntimeError('UNKNOWN_RESOLUTION_EVIDENCE_DRIFT')
    prior_review=(ROOT/binding['prior_incremental_review_path']).resolve()
    if not prior_review.is_relative_to(allowed) or not sha_matches(binding.get('prior_incremental_review_sha256'),prior_review):
        raise RuntimeError('UNKNOWN_RESOLUTION_PRIOR_REVIEW_DRIFT')
    resolution_data=json.loads(resolution.read_text(encoding='utf-8'))
    review_data=json.loads(review.read_text(encoding='utf-8'))
    normalization_path=(ROOT/'reports/real-ai-integration-027/qwen38-ledger-normalization.json').resolve()
    if not normalization_path.is_relative_to(allowed) or not normalization_path.exists():
        raise RuntimeError('UNKNOWN_RESOLUTION_NORMALIZATION_MISSING')
    normalization=json.loads(normalization_path.read_text(encoding='utf-8'))
    normalization_review=(ROOT/normalization['review_path']).resolve()
    if not normalization_review.is_relative_to(allowed) or not normalization_review.exists():
        raise RuntimeError('UNKNOWN_RESOLUTION_NORMALIZATION_REVIEW_MISSING')
    normalization_review_data=json.loads(normalization_review.read_text(encoding='utf-8'))
    request_id=binding['request_id']
    completions=[row for row in events if row.get('event')=='COMPLETED' and row.get('request_id')==request_id]
    receipts=[row for row in events if row.get('event')=='REVIEWED_UNKNOWN_RESOLUTION' and row.get('request_id')==request_id]
    ledger_bytes=ledger_path.read_bytes()
    receipt_offsets=[]
    receipt_ends=[]
    receipt_lines=[]
    offset=0
    for line in ledger_bytes.splitlines(keepends=True):
        row=json.loads(line)
        if row.get('event')=='REVIEWED_UNKNOWN_RESOLUTION' and row.get('request_id')==request_id:
            receipt_offsets.append(offset)
            receipt_ends.append(offset+len(line))
            receipt_lines.append(line)
        offset+=len(line)
    prefix_rows=[]
    if len(receipt_offsets)==1:
        prefix_rows=[json.loads(line) for line in ledger_bytes[:receipt_offsets[0]].splitlines() if line.strip()]
    if (
        binding.get('decision')!='ALLOW_DIFFERENT_FROZEN_CANDIDATE_AFTER_REVIEWED_CURRENT_EXIT'
        or len(completions)!=1 or completions[0].get('remote_completion')!='UNKNOWN'
        or resolution_data.get('request_id')!=request_id
        or resolution_data.get('historical_immediate_remote_completion')!='UNKNOWN'
        or resolution_data.get('current_execution_state')!='NOT_EXECUTING'
        or review_data.get('verdict')!='PASS_SCOPED_REPLACEMENT_RESERVATION'
        or not sha_matches(review_data.get('binding_sha256'),binding_path)
        or len(receipts)!=1
        or len(receipt_offsets)!=1
        or not isinstance(binding.get('ledger_prefix_sha256'),str)
        or re.fullmatch('[0-9a-fA-F]{64}',binding['ledger_prefix_sha256']) is None
        or not sha_matches(receipts[0].get('binding_sha256'),binding_path)
        or not sha_matches(receipts[0].get('review_sha256'),review)
        or normalization.get('decision')!='ACCEPT_LINE_ENDING_NORMALIZATION_WITH_IDENTICAL_LEDGER_EVENTS'
        or normalization.get('request_id')!=request_id
        or normalization.get('parent_binding_sha256','').lower()!=sha(binding_path)
        or normalization.get('parent_review_sha256','').lower()!=sha(review)
        or normalization.get('original_raw_prefix_sha256','').lower()!=binding['ledger_prefix_sha256'].lower()
        or normalization.get('normalized_raw_prefix_sha256','').lower()!=hashlib.sha256(ledger_bytes[:receipt_offsets[0]]).hexdigest()
        or normalization.get('canonical_prefix_events_sha256','').lower()!=canonical_rows_sha256(prefix_rows)
        or normalization.get('pre_receipt_event_count')!=len(prefix_rows)
        or normalization.get('receipt_line_sha256','').lower()!=hashlib.sha256(receipt_lines[0]).hexdigest()
        or normalization.get('ledger_through_receipt_sha256','').lower()!=hashlib.sha256(ledger_bytes[:receipt_ends[0]]).hexdigest()
        or normalization_review_data.get('verdict')!='PASS_SCOPED_LEDGER_NORMALIZATION'
        or not sha_matches(normalization_review_data.get('normalization_sha256'),normalization_path)
    ):
        raise RuntimeError('UNKNOWN_RESOLUTION_SEMANTICS')
    return frozenset({request_id})
def protocol_source_hashes_match(left,right):
    """Collector-only resume changes must not relax model/protocol source binding."""
    left=dict(left);right=dict(right)
    left.pop(COLLECTOR_PATH,None);right.pop(COLLECTOR_PATH,None)
    return left==right
def model_input_contract_sha256():
    encoded=json.dumps(MODEL_INPUT_CONTRACT,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(encoded).hexdigest()
def bound_runtime_identity(runtime):
    pid=int(runtime['pid'])
    command=(
        f"$p=Get-Process -Id {pid} -ErrorAction Stop; "
        "[ordered]@{pid=$p.Id;path=$p.Path;started_utc=$p.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress"
    )
    actual=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',command],text=True))
    expected=datetime.fromisoformat(runtime['launched_at_utc'].replace('Z','+00:00'))
    observed=datetime.fromisoformat(actual['started_utc'].replace('Z','+00:00'))
    if actual['pid']!=pid or Path(actual['path']).resolve()!=Path(runtime['executable']).resolve() or abs((observed-expected).total_seconds())>1:
        raise RuntimeError('MODEL_RUNTIME_IDENTITY_CHANGED')
    return actual
def loopback_listener_closed(host='127.0.0.1',port=11435):
    """Return True only when the loopback listener is explicitly refused."""
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        probe.settimeout(.25);code=probe.connect_ex((host,port))
    if code in {10061,111}:return True
    if code==0:return False
    raise RuntimeError(f'MODEL_RUNTIME_LISTENER_STATUS_UNKNOWN_{code}')
def terminate_bound_runtime(runtime):
    """Stop only the exact project-owned Ollama service tree after a guard breach."""
    actual=bound_runtime_identity(runtime)
    pid=int(runtime['pid'])
    stopped=subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],capture_output=True,text=True,timeout=15)
    if stopped.returncode!=0:raise RuntimeError('MODEL_RUNTIME_TREE_TERMINATION_FAILED')
    for _ in range(40):
        try:bound_runtime_identity(runtime)
        except (RuntimeError,subprocess.CalledProcessError):break
        time.sleep(.25)
    else:raise RuntimeError('MODEL_RUNTIME_EXIT_UNCONFIRMED')
    for _ in range(40):
        if loopback_listener_closed():break
        time.sleep(.25)
    else:raise RuntimeError('MODEL_RUNTIME_LISTENER_STILL_AVAILABLE')
    return {'verified_before_stop':actual,'stopped':{'pid':pid,'tree_exit_confirmed':True,'listener_exit_confirmed':True}}
def runtime_guard_reason(*,now,deadline,observed,observation_error,ram_floor):
    if now>=deadline:return 'REQUEST_WALL_OR_CUMULATIVE_DEADLINE'
    if observation_error is not None:return 'RESOURCE_OBSERVATION_FAILED'
    if observed['ram_free_mib']<ram_floor or observed['gpu_free_mib']<2000:return 'RESOURCE_FLOOR_CROSSED'
    return None
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
    parser=argparse.ArgumentParser();parser.add_argument('--round',type=int,choices=range(4));parser.add_argument('--model',choices=('qwen3.5:4b','qwen3.5:9b',TWENTY_SEVEN_B_MODEL,QWEN38_MODEL),default='qwen3.5:4b');parser.add_argument('--phase',choices=('screen','fixed','novel'));parser.add_argument('--candidate',type=int,choices=(1,2,3,4));parser.add_argument('--resume-incomplete-screen',action='store_true');parser.add_argument('--resume-screen-collector-failure',action='store_true');parser.add_argument('--resume-resource-adjusted-screen',action='store_true');args=parser.parse_args()
    bounded_candidate=args.model!='qwen3.5:4b'
    twenty_seven_b=args.model==TWENTY_SEVEN_B_MODEL
    qwen38=args.model==QWEN38_MODEL
    large_candidate=twenty_seven_b or qwen38
    screen_ids=QWEN38_SCREEN_IDS if qwen38 else SCREEN_IDS
    candidate_source_binding=None
    if args.model=='qwen3.5:4b':
        assert args.round is not None and args.phase is None and args.candidate is None,'Historical 4B mode requires --round only'
        target=OUT/f'quality28-round{args.round}.json';run_out=OUT;max_requests=32
    else:
        assert args.round is None and args.phase is not None and args.candidate is not None,'Bounded candidate mode requires --phase and --candidate'
        if twenty_seven_b:assert args.candidate==1,'Retired Q4 27B has one historical candidate'
        if qwen38:assert args.candidate in {2,3,4},'Qwen3.8 may only continue its bounded protocol candidates'
        assert args.candidate==1 or qwen38 or args.phase in {'fixed','novel'},'Later candidates cannot add another screen after the bounded repair'
        assert qwen38 or args.candidate!=3 or args.phase in {'fixed','novel'},'Delivery v11 candidate uses one complete fixed run and one novel run'
        assert sum((args.resume_incomplete_screen,args.resume_screen_collector_failure,args.resume_resource_adjusted_screen))<=1,'Only one bounded screen recovery is allowed'
        assert not (args.resume_incomplete_screen or args.resume_screen_collector_failure) or (args.candidate==1 and args.phase=='screen'),'Only candidate-01 screen can resume'
        assert not args.resume_resource_adjusted_screen or (qwen38 and args.phase=='screen'),'Only Qwen3.8 can run the reviewed resource-adjusted screen'
        budget_root=(ROOT/'reports/real-ai-integration-027/diagnostic-r3-27b' if large_candidate else
                     ROOT/'reports/real-ai-integration-027/diagnostic-r3-9b-v11'
                     if args.candidate==3 else ROOT/'reports/real-ai-integration-027/diagnostic-r3-9b')
        active_call_limit=(TWENTY_SEVEN_B_TOTAL_CALL_LIMIT if large_candidate else
                           DELIVERY_V11_TOTAL_CALL_LIMIT if args.candidate==3 else HISTORICAL_NINE_B_TOTAL_CALL_LIMIT)
        run_out=budget_root/f'candidate-{args.candidate:02d}';run_out.mkdir(parents=True,exist_ok=True)
        phase_label=('screen-g52' if args.resume_resource_adjusted_screen else
                     'screen-continuation-recovery' if args.resume_screen_collector_failure else
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
    overlay=(json.loads(overlay_path.read_text(encoding='utf-8')) if bounded_candidate else None)
    novel_path=(ROOT/'reports/real-ai-integration-027/qwen38-v12-novel-set.json'
                if qwen38 and args.candidate>=3 else ROOT/'reports/real-ai-integration-027/qwen35-9b-novel-set.json')
    novel=(json.loads(novel_path.read_text(encoding='utf-8')) if bounded_candidate else None)
    if overlay is not None:
        assert overlay['base_gold_unchanged'] is True and set(overlay['cases'])=={item['id'] for item in gold['cases']}
    all_items=gold['cases']+photos['cases']
    if bounded_candidate and args.phase=='screen':
        if args.resume_resource_adjusted_screen:
            prior=json.loads((run_out/'screen-resource-adjusted.json').read_text(encoding='utf-8'))
            assert prior['status']=='INCOMPLETE' and prior.get('stop_reason')=='RESOURCE_FLOOR_CROSSED'
            assert prior['attempted_requests']==1 and prior['results'][0]['id']=='PHOTO-002'
            by_id={item['id']:item for item in all_items}
            selected_items=[by_id[case_id] for case_id in screen_ids]
        elif args.resume_screen_collector_failure:
            selected_items=incomplete_collector_recovery_items(
                all_items,json.loads((run_out/'screen-continuation.json').read_text(encoding='utf-8'))
            )
        elif args.resume_incomplete_screen:
            selected_items=incomplete_screen_resume_items(all_items,json.loads((run_out/'screen.json').read_text(encoding='utf-8')))
        else:
            by_id={item['id']:item for item in all_items}
            selected_items=[by_id[case_id] for case_id in screen_ids]
            assert tuple(item['id'] for item in selected_items)==screen_ids
    elif bounded_candidate and args.phase=='novel':
        frozen_key=('frozen_before_first_inference' if qwen38 and args.candidate>=3
                    else 'frozen_before_first_9b_inference')
        assert novel[frozen_key] is True and len(novel['cases'])==4
        selected_items=novel['cases']
    else:selected_items=all_items
    downloads=json.loads((OUT/'repair-image-downloads.json').read_text(encoding='utf-8'))
    bounded_manifest_path=ROOT/'reports/real-ai-integration-027/qwen35-9b-bounded-images.json'
    bounded_manifest=(json.loads(bounded_manifest_path.read_text(encoding='utf-8'))
                      if bounded_candidate and (large_candidate or args.candidate>=2) else None)
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
    if args.model=='qwen3.5:4b':
        digest=json.loads((OUT/'download-4b-result.json').read_text(encoding='utf-8'))['manifest_sha256']
    elif args.model=='qwen3.5:9b':
        digest='6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
    elif twenty_seven_b:
        candidate_source=json.loads(TWENTY_SEVEN_B_CANDIDATE_SOURCE.read_text(encoding='utf-8'))
        assert candidate_source['model']==TWENTY_SEVEN_B_MODEL and candidate_source['source']=='official Ollama library'
        digest=candidate_source['digest']
        assert re.fullmatch('[0-9a-f]{64}',digest),'Invalid pinned 27B digest'
        assert isinstance(candidate_source['size_bytes'],int) and 0<candidate_source['size_bytes']<=20*1024**3
        candidate_source_binding={'path':str(TWENTY_SEVEN_B_CANDIDATE_SOURCE),'sha256':sha(TWENTY_SEVEN_B_CANDIDATE_SOURCE)}
    else:
        candidate_source=json.loads(QWEN38_CANDIDATE_SOURCE.read_text(encoding='utf-8'))
        assert candidate_source['source_type']=='community_quantization'
        assert candidate_source['revision']=='7af3b8fef254c9246345e0aa6dfc5ea8b5c45f02'
        assert candidate_source['ollama_model']==QWEN38_MODEL
        digest=candidate_source['ollama_manifest_digest']
        assert re.fullmatch('[0-9a-f]{64}',digest),'Invalid pinned Qwen3.8 manifest digest'
        assert candidate_source['model_file']['sha256']=='fa09d2fbe7d10fe6d91d3e647bd5a2782ec028c2acd9588dd73c9757f82d9631'
        assert candidate_source['vision_projector']['sha256']=='9793ab158aefdce41d531d37a5458287dcdfc2d10585f4af106802810dfc2618'
        assert 0<candidate_source['total_runtime_asset_bytes']<=20*1024**3
        candidate_source_binding={'path':str(QWEN38_CANDIDATE_SOURCE),'sha256':sha(QWEN38_CANDIDATE_SOURCE)}
    expected_version='0.34.2' if args.model=='qwen3.5:4b' else '0.34.3'
    assert api('/api/version')['version']==expected_version
    assert next(r for r in api('/api/tags')['models'] if r['name']==args.model)['digest']==digest
    def resident():
        rows=api('/api/ps')['models']
        assert len(rows)<=1 and all(r['name']==args.model and r['digest']==digest and r['context_length']==4096 for r in rows),'Resident model/context mismatch'
        return rows
    runtime_path=(ROOT/'reports/real-ai-integration-027/qwen35-9b-runtime-resume.json')
    runtime=(json.loads((OUT/'diagnostic-runtime28.json').read_text(encoding='utf-8-sig')) if args.model=='qwen3.5:4b' else
             json.loads(QWEN38_RUNTIME.read_text(encoding='utf-8-sig')) if qwen38 else
             json.loads(TWENTY_SEVEN_B_RUNTIME.read_text(encoding='utf-8-sig')) if twenty_seven_b else
             json.loads((runtime_path if runtime_path.exists() else ROOT/'reports/real-ai-integration-027/qwen35-9b-runtime.json').read_text(encoding='utf-8-sig')))
    assert runtime['no_cloud'] and runtime['context_length']==4096 and runtime['concurrency']==1
    runtime_identity=bound_runtime_identity(runtime) if large_candidate else None
    budget_path=(budget_root/'qwen-budget.jsonl') if bounded_candidate else run_out/'qwen-budget.jsonl'
    def budget_events():
        if not budget_path.exists():return []
        return [json.loads(line) for line in budget_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    def budget_state():
        events=budget_events()
        reviewed=(reviewed_resolution_ids(events,QWEN38_UNKNOWN_RESOLUTION_BINDING,budget_path) if qwen38 else frozenset())
        return budget_state_from_events(events,reviewed)
    def append_budget(row):
        payload=(json.dumps({'at_utc':datetime.now(UTC).isoformat(),**row},ensure_ascii=False,separators=(',',':'))+'\n').encode()
        fd=os.open(budget_path,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:os.write(fd,payload);os.fsync(fd)
        finally:os.close(fd)
    request_timeout=TWENTY_SEVEN_B_REQUEST_TIMEOUT_SECONDS if large_candidate else 60
    if bounded_candidate:
        state=budget_state();assert not state['unknown'],f'Prior request completion UNKNOWN: {state["unknown"]}'
        assert state['attempted']<active_call_limit and state['model_seconds']+request_timeout<=NINE_B_TOTAL_MODEL_SECONDS
        assert datetime.now(UTC)+timedelta(seconds=request_timeout)<=NINE_B_DEADLINE_UTC,'Insufficient bounded time before the delivery deadline'
    e={'task_id':'REAL-AI-INTEGRATION-027' if bounded_candidate else 'SELFHOST-AI-LOCAL-001','round':args.round,'phase':phase_label if bounded_candidate else args.phase,'candidate':args.candidate,'repair_rounds_used':args.round,'base_sha':'e5071877fcd8bea3f0c732e91101e4137d25f445',
       'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'at_utc':datetime.now(UTC).isoformat(),
       'model':args.model,'model_digest':digest,'quantization':'Q3_K_M' if qwen38 else 'Q4_K_M','prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,
       'candidate_source_binding':candidate_source_binding,
       'vision_prompt_version':getattr(QwenVLMProvider,'prompt_version','unversioned-original'),
       'source_hashes':{p:sha(ROOT/p) for p in ['backend/app/service/analysis.py','backend/app/domain/ai.py','backend/app/domain/vision.py','backend/app/domain/observed_event.py','backend/app/provider/llm/__init__.py','backend/app/provider/vision/__init__.py','backend/app/provider/http.py','reports/auth-identity-ai-001/local-qwen/run-quality28.py']},
       'gold_hashes':{textpath.name:sha(textpath),photopath.name:sha(photopath),**({'qwen35-9b-evaluation-overlay.json':sha(overlay_path),novel_path.name:sha(novel_path)} if overlay is not None else {}),**({'qwen35-9b-bounded-images.json':sha(bounded_manifest_path)} if bounded_manifest is not None else {})},'runtime':runtime,
       'runtime_identity':runtime_identity,
       'image_input':({'mode':'proportional-jpeg-max-1024','manifest':str(bounded_manifest_path),'manifest_sha256':sha(bounded_manifest_path)} if bounded_manifest is not None else {'mode':'original-gold-files'}),
       'controls':{'context':4096,'temperature':0,'thinking':False,'llm_max_tokens':512,'vlm_max_tokens':256,'timeout_seconds':request_timeout,'guard_cleanup_margin_seconds':TWENTY_SEVEN_B_TERMINATION_MARGIN_SECONDS if large_candidate else 0,'parallel':1,'max_cases':1 if args.resume_screen_collector_failure else 7 if args.resume_incomplete_screen else 8 if args.phase=='screen' else 4 if args.phase=='novel' else 28,'max_inference_requests':max_requests},
       'model_input_contract_sha256':model_input_contract_sha256(),
       'cumulative_budget':{'path':str(budget_path),'call_limit':active_call_limit,'model_seconds_limit':NINE_B_TOTAL_MODEL_SECONDS,'deadline_utc':NINE_B_DEADLINE_UTC.isoformat()} if bounded_candidate else None,
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
        if bounded_candidate:
            state=budget_state();assert not state['unknown'],'Prior request completion UNKNOWN'
            assert state['attempted']<active_call_limit and state['model_seconds']+request_timeout<=NINE_B_TOTAL_MODEL_SECONDS
            assert datetime.now(UTC)+timedelta(seconds=request_timeout)<=NINE_B_DEADLINE_UTC,'Insufficient bounded time before the delivery deadline'
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
    client=httpx.Client(trust_env=False,timeout=request_timeout,event_hooks={'request':[request_hook],'response':[response_hook]})
    # Product Settings deliberately caps provider timeouts at 60 seconds.  The
    # authorized 27B diagnostic extends only its isolated HTTP client timeout;
    # it must not widen the application configuration contract.
    settings=Settings(_env_file=None,app_env='test',llm_provider='production',llm_base_url='http://127.0.0.1:11435/v1',llm_model=args.model,llm_api_key='local-loopback-no-auth',llm_disable_thinking=True,llm_max_output_tokens=512,llm_timeout_seconds=min(request_timeout,60))
    service=AnalysisService.__new__(AnalysisService);service._settings=settings;service._llm=build_llm_provider(settings);service._llm._client=client;service._clock=lambda:datetime.now(UTC)
    service._vlm=QwenVLMProvider(base_url='http://127.0.0.1:11435/v1',model=args.model,api_key='local-loopback-no-auth',timeout_seconds=request_timeout,storage=PhotoStorage(images),client=client,disable_thinking=True,max_output_tokens=256)
    logs=[];service._write_call_log=lambda *a,**kw:logs.append(kw)
    def stage(kind,call,case):
        nonlocal current
        loaded=resident();before=sample()
        required_gpu=((TWENTY_SEVEN_B_RESIDENT_REQUEST_GPU_FREE_MIB if large_candidate else NINE_B_RESIDENT_REQUEST_GPU_FREE_MIB) if loaded else
                      NINE_B_START_GPU_FREE_MIB if bounded_candidate else 6000)
        if before['ram_free_mib']<4500 or before['gpu_free_mib']<required_gpu:
            e['stop_reason']='RESOURCE_HEADROOM';e['guard_sample']=before;save();raise RuntimeError('RESOURCE_HEADROOM')
        if large_candidate and not loaded:
            usable_mib=(before['ram_free_mib']-4500)+(before['gpu_free_mib']-2000)
            candidate_bytes=(candidate_source['total_runtime_asset_bytes'] if qwen38 else candidate_source['size_bytes'])
            required_mib=math.ceil(candidate_bytes/1048576)+TWENTY_SEVEN_B_ADMISSION_OVERHEAD_MIB
            e['capacity_admission']={
                'model_size_bytes':candidate_bytes,
                'fixed_runtime_kv_image_overhead_mib':TWENTY_SEVEN_B_ADMISSION_OVERHEAD_MIB,
                'usable_ram_plus_gpu_above_floors_mib':usable_mib,
                'required_mib':required_mib,
            }
            if usable_mib<required_mib:
                e['stop_reason']='RESOURCE_CAPACITY';e['guard_sample']=before;save();raise RuntimeError('RESOURCE_CAPACITY')
        state_at_start=budget_state() if bounded_candidate else {'model_seconds':0}
        remaining_budget_seconds=NINE_B_TOTAL_MODEL_SECONDS-state_at_start['model_seconds']
        remaining_deadline_seconds=(NINE_B_DEADLINE_UTC-datetime.now(UTC)).total_seconds() if bounded_candidate else request_timeout
        allowed_wall_seconds=min(request_timeout,remaining_budget_seconds,remaining_deadline_seconds)-(TWENTY_SEVEN_B_TERMINATION_MARGIN_SECONDS if large_candidate else 0)
        if allowed_wall_seconds<=0:
            e['stop_reason']='REQUEST_TIME_BUDGET_EXHAUSTED';save();raise RuntimeError('REQUEST_TIME_BUDGET_EXHAUSTED')
        current={'kind':kind,'at_utc':datetime.now(UTC).isoformat(),'before':before,'resident_before':loaded,'samples':[],'attempted':False,'status':'STARTING','wall_limit_seconds':allowed_wall_seconds}
        case['stages'].append(current);save();stop=threading.Event();started=time.perf_counter();logs.clear()
        wall_deadline=time.monotonic()+allowed_wall_seconds
        def terminate_for_guard(reason,observed):
            if current.get('guard_stop_reason'):return
            current['guard_stop_reason']=reason;current['guard_sample']=observed
            if large_candidate:
                current['breach_action']='TERMINATE_EXACT_BOUND_PROJECT_MODEL_RUNTIME_TREE'
                try:current['runtime_stop']=terminate_bound_runtime(runtime)
                except Exception as exc:current['runtime_stop_error']=type(exc).__name__
            else:
                current['breach_action']='STOP_BEFORE_NEXT_RESERVATION'
        def monitor():
            while not stop.wait(.25):
                observed=None;observation_error=None
                try:
                    observed=sample();current['samples'].append(observed)
                except Exception as exc:
                    observation_error=type(exc).__name__;current['samples'].append({'error_type':observation_error})
                reason=runtime_guard_reason(
                    now=time.monotonic(),deadline=wall_deadline,observed=observed,
                    observation_error=observation_error,ram_floor=4500 if large_candidate else 2000,
                )
                if reason:
                    if reason=='RESOURCE_FLOOR_CROSSED':current['runtime_floor_breach']=observed
                    terminate_for_guard(reason,observed or {'error_type':observation_error})
                    break
        monitor_thread=threading.Thread(target=monitor,daemon=True);monitor_thread.start()
        try:
            answer=call();current['provider_result']=answer;current['status']='RECORDED'
            return answer
        except Exception as exc:
            current['status']='EXCEPTION';current['error_type']=type(exc).__name__
            raise
        finally:
            stop.set();monitor_thread.join(timeout=20);current['latency_seconds']=round(time.perf_counter()-started,3)
            current['call_log']=list(logs)
            try:current['after']=sample()
            except Exception as exc:current['after']={'error_type':type(exc).__name__}
            try:current['resident_after']=resident()
            except Exception as exc:
                current['resident_after']=None;current['resident_after_error']=type(exc).__name__
            measured=[before]+([current['after']] if 'ram_free_mib' in current['after'] else [])+[s for s in current['samples'] if 'ram_free_mib' in s]
            current['min_ram_free_mib']=min(s['ram_free_mib'] for s in measured);current['min_gpu_free_mib']=min(s['gpu_free_mib'] for s in measured)
            if bounded_candidate and current.get('budget_request_id'):
                controlled_stop=bool(current.get('runtime_stop',{}).get('stopped',{}).get('tree_exit_confirmed'))
                append_budget({'event':'COMPLETED','request_id':current['budget_request_id'],'candidate':args.candidate,'phase':e['phase'],'kind':kind,'status':current['status'],'model_seconds':current['latency_seconds'],'raw_response_sha256':current.get('raw_response_sha256'),'remote_completion':'RESPONSE_RECEIVED' if current.get('raw_response_sha256') else 'CONTROLLED_RUNTIME_TERMINATION_CONFIRMED' if controlled_stop else 'UNKNOWN'})
            if current.get('guard_stop_reason'):e['stop_reason']=current['guard_stop_reason']
            elif current.get('runtime_floor_breach') or current['min_ram_free_mib']<(4500 if large_candidate else 2000) or current['min_gpu_free_mib']<2000:e['stop_reason']='RESOURCE_FLOOR_CROSSED'
            save()
    try:
        save()
        reused={}
        reused_sources={}
        if bounded_candidate and args.phase=='fixed' and (args.candidate==1 or (qwen38 and args.candidate>=3)):
            screen_path=run_out/'screen.json';assert screen_path.exists(),'Complete identical screen phase is required before fixed phase'
            screen=json.loads(screen_path.read_text(encoding='utf-8'))
            if large_candidate:
                stable_controls=('context','temperature','thinking','llm_max_tokens','vlm_max_tokens','timeout_seconds','guard_cleanup_margin_seconds','parallel')
                assert screen['source_hashes']==e['source_hashes'],'27B collector or protocol source changed after screen'
                assert screen.get('candidate_source_binding')==e['candidate_source_binding'],'27B candidate source changed after screen'
                assert screen.get('model_input_contract_sha256')==e['model_input_contract_sha256'],'27B input contract changed after screen'
                assert screen['runtime']==e['runtime'],'27B runtime identity changed after screen'
                assert all(screen['controls'][key]==e['controls'][key] for key in stable_controls),'27B controls changed after screen'
                canonical={item['id']:item for item in all_items}
                assert all(row.get('input')==canonical.get(row.get('id')) for row in screen.get('results',[])),'27B canonical screen input changed'
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
            reused={row['id']:row for row in screen_rows};assert tuple(reused)==screen_ids and attempted==10
        if bounded_candidate and args.phase=='novel':
            fixed=json.loads((run_out/'fixed.json').read_text(encoding='utf-8'))
            assert fixed['status']=='RECORDED_NEEDS_SEMANTIC_REVIEW' and fixed['source_hashes']==e['source_hashes'] and fixed['gold_hashes']==e['gold_hashes']
            if large_candidate:
                stable_controls=('context','temperature','thinking','llm_max_tokens','vlm_max_tokens','timeout_seconds','guard_cleanup_margin_seconds','parallel')
                assert fixed['model']==args.model and fixed['model_digest']==digest,'27B model changed before novel set'
                assert fixed.get('candidate_source_binding')==e['candidate_source_binding'],'27B candidate source changed before novel set'
                assert fixed.get('model_input_contract_sha256')==e['model_input_contract_sha256'],'27B input contract changed before novel set'
                assert fixed['runtime']==e['runtime'] and fixed.get('runtime_identity')==e['runtime_identity'],'27B runtime changed before novel set'
                assert all(fixed['controls'][key]==e['controls'][key] for key in stable_controls),'27B controls changed before novel set'
        for item in selected_items:
            if item['id'] in reused:
                case=json.loads(json.dumps(reused[item['id']]))
                source_path=reused_sources[item['id']]
                case['reused_from']={'path':str(source_path),'source_sha256':sha(source_path),'protocol_source_hashes_verified':True,
                    'frozen_controls_and_canonical_input_verified':True,
                    'resume_binding_sha256':None if large_candidate else sha(NINE_B_RESUME_BINDING) if NINE_B_RESUME_BINDING.exists() else None}
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
        expected_cases=1 if args.resume_screen_collector_failure else 7 if args.resume_incomplete_screen else 8 if bounded_candidate and args.phase=='screen' else 4 if bounded_candidate and args.phase=='novel' else 28
        expected_calls=2 if args.resume_screen_collector_failure else 9 if args.resume_incomplete_screen else 10 if bounded_candidate and args.phase=='screen' else 4 if bounded_candidate and args.phase=='novel' else 32
        e['status']='RECORDED_NEEDS_SEMANTIC_REVIEW' if len(e['results'])==expected_cases and e['attempted_requests']+e['reused_requests']==expected_calls and not e.get('stop_reason') else 'INCOMPLETE'
    except Exception as exc:e['status']='INCOMPLETE';e.setdefault('stop_reason',type(exc).__name__);raise
    finally:
        client.close();e['finished_at_utc']=datetime.now(UTC).isoformat();save()
        print(json.dumps({'status':e['status'],'cases':len(e['results']),'requests':e['attempted_requests'],'stop_reason':e.get('stop_reason')}),flush=True)

if __name__=='__main__':main()
