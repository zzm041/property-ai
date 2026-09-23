"""Fixed 24-case read-only local inference; no DB, Worker or business API."""
from pathlib import Path
from datetime import UTC, datetime
import hashlib
import argparse
import json
import re
import runpy
import site
import subprocess
import sys
import threading
import time

ROOT=Path(r'D:\property-ai'); OUT=Path(__file__).parent
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
import httpx
from app.core.config import Settings
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION,WorkOrderAnalysisOutput,map_analysis_output
from app.provider.factory import build_llm_provider
from app.service.analysis import AnalysisService,AnalysisSnapshot

TARGET=OUT/'gold24-v3-results.json'
parser=argparse.ArgumentParser();parser.add_argument('--resume-zero-call-resource-block',action='store_true');args=parser.parse_args()
prior=json.loads(TARGET.read_text(encoding='utf-8')) if TARGET.exists() else None
if prior:
    assert args.resume_zero_call_resource_block and prior['results']==[] and prior['stop_reason']=='RESOURCE_HEADROOM','Never repeat recorded inference automatically'
helpers=runpy.run_path(str(OUT/'probe-model.py'));api=helpers['api'];sample=helpers['resource_sample']
goldpath=OUT/'gold-set-24.json';gold=json.loads(goldpath.read_text(encoding='utf-8'))
assert len(gold['cases'])==24 and WORK_ORDER_ANALYSIS_PROMPT_VERSION=='wo-analysis-v3'
download=json.loads((OUT/'download-4b-result.json').read_text());digest=download['manifest_sha256']
tag=next(row for row in api('/api/tags')['models'] if row['name']=='qwen3.5:4b')
assert tag['digest']==digest and download['hashes_verified'] and api('/api/version')['version']=='0.34.2'
assert all(row['name']=='qwen3.5:4b' for row in api('/api/ps')['models'])
if prior:
    assert prior['set_sha256']==hashlib.sha256(goldpath.read_bytes()).hexdigest() and prior['model_digest']==digest and prior['prompt_version']==WORK_ORDER_ANALYSIS_PROMPT_VERSION
    pre_resume=sample()
    if pre_resume['ram_free_mib']<4500 or pre_resume['gpu_free_mib']<6000:
        print(json.dumps({'resume':'STILL_BLOCKED_NO_CALL_NO_OVERWRITE','resource_sample':pre_resume}));sys.exit(0)
    archive=OUT/'gold24-v3-resource-blocked-initial.json';assert not archive.exists()
    archive.write_bytes(TARGET.read_bytes())
e={'task_id':'SELFHOST-AI-LOCAL-001','set_version':gold['set_version'],'set_sha256':hashlib.sha256(goldpath.read_bytes()).hexdigest(),
   'at_utc':datetime.now(UTC).isoformat(),'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
   'model':'qwen3.5:4b','model_digest':digest,'quantization':'Q4_K_M','prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,
   'request_controls':{'context':4096,'temperature':0,'thinking':False,'max_tokens':512,'timeout_seconds':60,'parallel':1,'max_calls':24},
   'model_switch':False,'business_calls':False,'database_access':False,'source_scope':'Working prompt/domain/LLM hashes below; local HTTP proxy fix WIP present but explicitly direct client used.',
   'source_hashes':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in ['backend/app/service/analysis.py','backend/app/domain/ai.py','backend/app/provider/llm/__init__.py','backend/app/provider/http.py']},'results':[]}
def save(): TARGET.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
model_settings=Settings(_env_file=None,app_env='test',llm_provider='production',llm_base_url='http://127.0.0.1:11435/v1',llm_model='qwen3.5:4b',llm_api_key='local-loopback-no-auth',llm_disable_thinking=True,llm_max_output_tokens=512,llm_timeout_seconds=60)
service=AnalysisService.__new__(AnalysisService);service._llm=build_llm_provider(model_settings);service._clock=lambda:datetime.now(UTC)
current={};raw_responses=[]
def capture_response(response):
    response.read();raw_responses.append(response.json())
client=httpx.Client(trust_env=False,timeout=60,event_hooks={'response':[capture_response]});service._llm._client=client
real=service._llm.structured_completion
def completion(*,prompt,prompt_version):
    current['full_prompt']=prompt;current['input_sha256']=hashlib.sha256(prompt.encode()).hexdigest()
    return real(prompt=prompt,prompt_version=prompt_version)
service._llm.structured_completion=completion
logs=[];service._write_call_log=lambda *a,**kw:logs.append(kw)
samples=[];stop=threading.Event()
def monitor():
    while not stop.wait(1):
        try: samples.append(sample())
        except Exception as exc: samples.append({'error_type':type(exc).__name__})
sampler=threading.Thread(target=monitor,daemon=True);sampler.start();save()
try:
    for case in gold['cases']:
        before=sample();samples.append(before);loaded=api('/api/ps')['models']
        assert all(row['name']=='qwen3.5:4b' for row in loaded)
        if before['ram_free_mib']<4500 or before['gpu_free_mib']<(2500 if loaded else 6000):
            e['stop_reason']='RESOURCE_HEADROOM';break
        current={'id':case['id'],'input':case,'baseline':before,'model_digest':digest,'prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION};raw_responses.clear();logs.clear()
        context={'house':{'property_name':'合成测试小区','building_name':'合成楼','unit_name':'合成单元','room_no':'测试房'},'reporter_relationships':['OWNER'],'recent_house_orders':[dict(zip(('category','status','summary'),row)) for row in case['history']]}
        snapshot=AnalysisSnapshot(work_order_id=0,tenant_id=701,property_id=702,house_id=703,building_id=704,analysis_id=0,analysis_version=1,work_order_version=1,input_hash=hashlib.sha256(case['report'].encode()).hexdigest(),text=case['report'],existing_asr_text=None,images=(),audio=(),context=context)
        started=time.perf_counter()
        payload,error,status=service._run_llm(snapshot,asr_text=None,vision_text=None,request_id='gold24-'+case['id'],attempt=1)
        current.update(raw_response=list(raw_responses),raw_payload=payload,error=error,status=status,latency_seconds=round(time.perf_counter()-started,3),call_log=list(logs),tokens=getattr(payload,'token_usage',None))
        try:
            validated=WorkOrderAnalysisOutput.model_validate(payload);mapped=map_analysis_output(validated)
            current['mechanical_checks']={'schema':True,'chinese_summary':bool(re.search('[\u4e00-\u9fff]',validated.summary)) and '\n' not in validated.summary,
                'category_skill_risk':any(validated.category==c and validated.required_skill==s and validated.risk in risks for c,s,risks in case['accept']),
                'low_confidence_if_required':mapped.low_confidence if case['manual']=='LOW' else None,'role':validated.required_role=='STAFF',
                'severity':validated.severity,'priority':validated.priority,'mapped_priority':mapped.priority.value}
        except Exception as exc: current['mechanical_checks']={'schema':False,'error_type':type(exc).__name__}
        current['semantic_review']='PENDING_INDIVIDUAL_REVIEW_NOT_AUTOMATIC_PASS';e['results'].append(current);save()
        print(json.dumps({'id':case['id'],'seconds':current['latency_seconds'],'checks':current['mechanical_checks'],'summary':payload.get('summary') if payload else None},ensure_ascii=False),flush=True)
        valid=[row for row in samples if 'ram_free_mib' in row]
        if any(row['ram_free_mib']<2000 or row['gpu_free_mib']<2000 for row in valid): e['stop_reason']='RESOURCE_FLOOR_CROSSED_STOP_FURTHER_CALLS';break
    e['status']='24_CALLS_RECORDED_REQUIRES_SEMANTIC_REVIEW' if len(e['results'])==24 else 'BOUNDED_RUN_INCOMPLETE'
finally:
    stop.set();sampler.join(timeout=10);client.close();e['resource_samples']=samples
    valid=[row for row in samples if 'ram_free_mib' in row];e['min_ram_free_mib']=min(row['ram_free_mib'] for row in valid) if valid else None;e['min_gpu_free_mib']=min(row['gpu_free_mib'] for row in valid) if valid else None
    save();print(json.dumps({'status':e.get('status'),'completed':len(e['results']),'stop_reason':e.get('stop_reason')}),flush=True)
