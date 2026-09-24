"""Bounded real 28-case diagnostics; existing provider pipeline, no business IO."""
from pathlib import Path
from datetime import UTC, datetime
import argparse
import hashlib
import json
import math
import re
import runpy
import site
import subprocess
import sys
import threading
import time

ROOT=Path(r'D:\property-ai');OUT=Path(__file__).parent
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'backend'))
import httpx
from app.core.config import Settings
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION,WorkOrderAnalysisOutput,map_analysis_output
from app.provider.factory import build_llm_provider
from app.provider.vision import QwenVLMProvider
from app.service.analysis import AnalysisService,AnalysisSnapshot,MediaRef

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
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
    parser=argparse.ArgumentParser();parser.add_argument('--round',type=int,choices=range(4),required=True);args=parser.parse_args()
    target=OUT/f'quality28-round{args.round}.json'
    assert not target.exists(),'Recorded round is immutable: inspect or explicit new repair round; no replay'
    helpers=runpy.run_path(str(OUT/'probe-model.py'));api=helpers['api'];sample=helpers['resource_sample']
    textpath=OUT/'gold-set-24.json';photopath=OUT/'repair-image-gold.json'
    assert sha(textpath)=='c3c93b419b7d5dea12b529152cde661c16826cc708b1d80d5b26d39d70ddfc1f'
    assert sha(photopath)=='df10abfde5a0144c480e29041ddedcc146a0182a6452dacd2ed97498c88cfdbe'
    gold=json.loads(textpath.read_text(encoding='utf-8'));photos=json.loads(photopath.read_text(encoding='utf-8'))
    assert len(gold['cases'])==24 and len(photos['cases'])==4
    downloads=json.loads((OUT/'repair-image-downloads.json').read_text(encoding='utf-8'))
    images={}
    for p in photos['cases']:
        path=OUT/'repair-images'/p['file'];row=next(r for r in downloads if Path(r['file']).name==p['file'])
        assert sha(path)==row['sha256'] and p['gps_metadata_present'] is False
        images[p['file']]=path.read_bytes()
    digest=json.loads((OUT/'download-4b-result.json').read_text(encoding='utf-8'))['manifest_sha256']
    assert api('/api/version')['version']=='0.34.2'
    assert next(r for r in api('/api/tags')['models'] if r['name']=='qwen3.5:4b')['digest']==digest
    def resident():
        rows=api('/api/ps')['models']
        assert len(rows)<=1 and all(r['name']=='qwen3.5:4b' and r['digest']==digest and r['context_length']==4096 for r in rows),'Resident model/context mismatch'
        return rows
    runtime=json.loads((OUT/'diagnostic-runtime28.json').read_text(encoding='utf-8-sig'))
    assert runtime['no_cloud'] and runtime['context_length']==4096 and runtime['concurrency']==1
    e={'task_id':'SELFHOST-AI-LOCAL-001','round':args.round,'repair_rounds_used':args.round,'base_sha':'13786627b05120613ba4649da14be606cd58c10e',
       'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'at_utc':datetime.now(UTC).isoformat(),
       'model':'qwen3.5:4b','model_digest':digest,'quantization':'Q4_K_M','prompt_version':WORK_ORDER_ANALYSIS_PROMPT_VERSION,
       'vision_prompt_version':getattr(QwenVLMProvider,'prompt_version','unversioned-original'),
       'source_hashes':{p:sha(ROOT/p) for p in ['backend/app/service/analysis.py','backend/app/domain/ai.py','backend/app/provider/llm/__init__.py','backend/app/provider/vision/__init__.py','backend/app/provider/http.py','reports/auth-identity-ai-001/local-qwen/run-quality28.py']},
       'gold_hashes':{textpath.name:sha(textpath),photopath.name:sha(photopath)},'runtime':runtime,
       'controls':{'context':4096,'temperature':0,'thinking':False,'llm_max_tokens':512,'vlm_max_tokens':256,'timeout_seconds':60,'parallel':1,'max_cases':28,'max_inference_requests':32},
       'business_calls':False,'database_access':False,'queue_access':False,'business_runtime_changed':False,'attempted_requests':0,'results':[],'status':'RUNNING'}
    def save():target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    current={}
    def request_hook(request):
        assert str(request.url)=='http://127.0.0.1:11435/v1/chat/completions' and request.method=='POST'
        body=json.loads(request.content);assert body['model']=='qwen3.5:4b' and body['temperature']==0 and body['reasoning_effort']=='none'
        assert body['max_tokens']==(256 if current['kind']=='VLM' else 512)
        assert e['attempted_requests']<32
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
    settings=Settings(_env_file=None,app_env='test',llm_provider='production',llm_base_url='http://127.0.0.1:11435/v1',llm_model='qwen3.5:4b',llm_api_key='local-loopback-no-auth',llm_disable_thinking=True,llm_max_output_tokens=512,llm_timeout_seconds=60)
    service=AnalysisService.__new__(AnalysisService);service._llm=build_llm_provider(settings);service._llm._client=client;service._clock=lambda:datetime.now(UTC)
    service._vlm=QwenVLMProvider(base_url='http://127.0.0.1:11435/v1',model='qwen3.5:4b',api_key='local-loopback-no-auth',timeout_seconds=60,storage=PhotoStorage(images),client=client,disable_thinking=True,max_output_tokens=256)
    logs=[];service._write_call_log=lambda *a,**kw:logs.append(kw)
    def stage(kind,call,case):
        nonlocal current
        loaded=resident();before=sample()
        if before['ram_free_mib']<4500 or before['gpu_free_mib']<(2500 if loaded else 6000):
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
            if current['min_ram_free_mib']<2000 or current['min_gpu_free_mib']<2000:e['stop_reason']='RESOURCE_FLOOR_CROSSED'
            save()
    try:
        save()
        for item in gold['cases']+photos['cases']:
            is_photo=item['id'].startswith('PHOTO');report=item['paired_report'] if is_photo else item['report']
            case={'id':item['id'],'input':item,'stages':[],'semantic_review':'PENDING_INDIVIDUAL_REVIEW_NOT_AUTOMATIC_PASS'};e['results'].append(case)
            context={'house':{'property_name':'合成测试小区','building_name':'合成楼','unit_name':'合成单元','room_no':'测试房'},'reporter_relationships':['OWNER'],'recent_house_orders':[dict(zip(('category','status','summary'),r)) for r in item.get('history',[])]}
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
            try:
                valid=WorkOrderAnalysisOutput.model_validate(payload);mapped=map_analysis_output(valid)
                case['mechanical_checks']={'schema_and_mapping':True,'has_cjk_hint':bool(re.search('[\u4e00-\u9fff]',valid.summary)),
                    'summary':valid.summary,'severity':valid.severity,'priority':valid.priority,'risk':valid.risk,'required_role':valid.required_role,'required_skill':valid.required_skill,'confidence':valid.confidence,
                    'below_confidence_threshold':valid.confidence<gold['common_gold']['confidence_threshold'],'business_fallback':mapped.low_confidence,
                    'category_skill_risk':any(valid.category==c and valid.required_skill==s and valid.risk in risks for c,s,risks in item['accept']) if not is_photo else None}
            except Exception as exc:case['mechanical_checks']={'schema_and_mapping':False,'error_type':type(exc).__name__}
            save();print(json.dumps({'case':item['id'],'requests':e['attempted_requests'],'payload':payload},ensure_ascii=False),flush=True)
            if e.get('stop_reason'):break
        e['status']='RECORDED_NEEDS_SEMANTIC_REVIEW' if len(e['results'])==28 and e['attempted_requests']==32 and not e.get('stop_reason') else 'INCOMPLETE'
    except Exception as exc:e['status']='INCOMPLETE';e.setdefault('stop_reason',type(exc).__name__);raise
    finally:
        client.close();e['finished_at_utc']=datetime.now(UTC).isoformat();save()
        print(json.dumps({'status':e['status'],'cases':len(e['results']),'requests':e['attempted_requests'],'stop_reason':e.get('stop_reason')}),flush=True)

if __name__=='__main__':main()
