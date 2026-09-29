"""Bounded AUTH001 local AI adapter, not a general model server.

One loopback listener, one owned CPU child at a time, fixed offline weights.
Append-only request reservations survive restarts. Health never loads a model.
All request/response bodies remain in the private run directory, not HTTP logs.
"""
from datetime import UTC, datetime
import argparse
import base64
import ctypes
from email import policy
from email.parser import BytesParser
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
import wave

sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from local_ai_models import MODEL_DIRS,offline,load,infer
from local_ai_process import RequestJob,join_request_job
from local_ai_runtime_control import (
    bound_runtime_identity,
    loopback_listener_pid,
    terminate_bound_runtime,
)
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.observed_event import work_order_analysis_response_format
from app.domain.vision import VISION_ANALYSIS_PROMPT_VERSION,vision_analysis_response_format

QWEN_CANDIDATES={
    'qwen3.5:4b':('2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd','0.34.3'),
    'qwen3.5:9b':('6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7','0.34.3'),
    'qwen38-27b-q3km-g55:7af3b8f':('eb18a122286f7a67d36dc8129f9caa1c0f7bb8449d7669a79e3ecb3d81289e0c','0.34.3'),
}
QWEN_MODEL=os.environ.get('PROPERTY_AI_LOCAL_QWEN_MODEL','qwen3.5:4b')
if QWEN_MODEL not in QWEN_CANDIDATES:raise RuntimeError('UNAPPROVED_QWEN_CANDIDATE')
DIGEST,QWEN_RUNTIME_VERSION=QWEN_CANDIDATES[QWEN_MODEL]
MODELS={'asr':'Qwen3-ASR-0.6B','embedding':'bge-m3','reranker':'bge-reranker-v2-m3','qwen':QWEN_MODEL}
LIMITS={'asr':1,'embedding':24,'reranker':18,'qwen':18}
CONTINUATION_MAX={'asr':1,'embedding':6,'reranker':6,'qwen':6}
MAX_BODY=1_100_000
WORK_ORDER_PROMPT_VERSION=WORK_ORDER_ANALYSIS_PROMPT_VERSION
WORK_ORDER_RESPONSE_FORMAT=work_order_analysis_response_format()
VISION_RESPONSE_FORMAT=vision_analysis_response_format()

def stamp():return datetime.now(UTC).isoformat()
def save(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
def ram():
    class Mem(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ('total','available','page','available_page','virtual','available_virtual','extended')]
    s=Mem();s.length=ctypes.sizeof(s)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):raise RuntimeError('RAM_CHECK_FAILED')
    return s.available//1048576
def gpu():
    raw=subprocess.check_output(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],timeout=3,text=True)
    values=[int(v.strip()) for v in raw.splitlines()]
    if len(values)!=1:raise RuntimeError('GPU_CHECK_FAILED')
    return values[0]
def direct(path,body=None,timeout=3):
    url='http://127.0.0.1:11435'+path
    request=urllib.request.Request(url,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=timeout) as r:
        return json.loads(r.read(1_100_000))
def qwen_guard():
    if direct('/api/version').get('version')!=QWEN_RUNTIME_VERSION:raise RuntimeError('QWEN_VERSION_CHANGED')
    tags=direct('/api/tags')['models']
    if not any(x['name']==MODELS['qwen'] and x['digest']==DIGEST for x in tags):raise RuntimeError('QWEN_DIGEST_CHANGED')
    loaded=direct('/api/ps')['models']
    if len(loaded)>1 or any(x['name']!=MODELS['qwen'] or x['digest']!=DIGEST or x['context_length']!=4096 for x in loaded):raise RuntimeError('QWEN_RESIDENCY_CHANGED')
    if ram()<4500 or gpu()<(2500 if loaded else 6000):raise RuntimeError('QWEN_RESOURCE_GUARD')

def validate(kind,value):
    if kind=='asr':
        if not isinstance(value,bytes) or len(value)>1_000_000:raise ValueError('AUDIO_SIZE')
        try:
            with wave.open(io.BytesIO(value),'rb') as w:
                if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getframerate()!=16000 or not 0<w.getnframes()<=480000 or w.getcomptype()!='NONE':raise ValueError('PCM_REQUIRED')
                if len(w.readframes(w.getnframes()))!=w.getnframes()*2:raise ValueError('TRUNCATED_AUDIO')
        except (wave.Error,EOFError) as exc:raise ValueError('PCM_REQUIRED') from exc
    elif kind=='embedding':
        if not isinstance(value,list) or not 1<=len(value)<=16 or any(not isinstance(v,str) or not 0<len(v)<=4000 for v in value):raise ValueError('EMBEDDING_INPUT')
    elif kind=='reranker':
        if not isinstance(value,dict) or set(value)!={'query','documents'} or not isinstance(value['query'],str) or not 0<len(value['query'])<=1000:raise ValueError('RERANK_INPUT')
        validate('embedding',value['documents'])
    elif kind=='qwen':
        expected={'model','messages','response_format','temperature','reasoning_effort','max_tokens'}
        if not isinstance(value,dict) or set(value)!=expected or value['model']!=MODELS['qwen'] or value['temperature']!=0 or value['reasoning_effort']!='none':raise ValueError('QWEN_SETTINGS')
        expected_tokens=256 if value['response_format']==VISION_RESPONSE_FORMAT else 512
        if value['max_tokens']!=expected_tokens:raise ValueError('QWEN_SETTINGS')
        messages=value['messages']
        if not isinstance(messages,list) or len(messages)!=1 or set(messages[0])!={'role','content'} or messages[0]['role']!='user':raise ValueError('QWEN_INPUT')
        content=messages[0]['content']
        if value['response_format']==VISION_RESPONSE_FORMAT:
            if not isinstance(content,list) or len(content)!=2:raise ValueError('VLM_INPUT')
            text_part,image_part=content
            if set(text_part)!={'type','text'} or text_part['type']!='text' or not isinstance(text_part['text'],str) or not 0<len(text_part['text'])<=1000:raise ValueError('VLM_INPUT')
            if set(image_part)!={'type','image_url'} or image_part['type']!='image_url' or set(image_part['image_url'])!={'url'}:raise ValueError('VLM_INPUT')
            image_url=image_part['image_url']['url']
            if not isinstance(image_url,str) or not image_url.startswith('data:image/') or ';base64,' not in image_url:raise ValueError('VLM_INPUT')
        elif not isinstance(content,str) or not 0<len(content)<=10000:raise ValueError('QWEN_INPUT')

def decode(path,content_type,body,prompt_version,scope='all'):
    if path=='/v1/audio/transcriptions':
        if not content_type.startswith('multipart/form-data;'):raise ValueError('MULTIPART_REQUIRED')
        msg=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
        parts=list(msg.iter_parts());names=[p.get_param('name',header='content-disposition') for p in parts]
        if sorted(names)!=['file','model']:raise ValueError('AUDIO_FIELDS')
        fields={n:p.get_payload(decode=True) for n,p in zip(names,parts)}
        if fields['model'].decode()!=MODELS['asr']:raise ValueError('MODEL_NOT_ALLOWED')
        kind='asr';value=fields['file']
    else:
        if content_type.split(';')[0]!='application/json':raise ValueError('JSON_REQUIRED')
        obj=json.loads(body)
        if path=='/v1/embeddings':
            if set(obj)!={'model','input'} or obj['model']!=MODELS['embedding']:raise ValueError('MODEL_OR_FIELDS')
            kind='embedding';value=obj['input']
        elif path=='/v1/rerank':
            if set(obj)!={'model','query','documents'} or obj['model']!=MODELS['reranker']:raise ValueError('MODEL_OR_FIELDS')
            kind='reranker';value={k:obj[k] for k in ('query','documents')}
        elif path=='/v1/chat/completions':
            if scope=='rag_only' and prompt_version!='rag-answer-v2':
                raise ValueError('RAG_ONLY_NO_AUTO_AUTHORIZATION')
            if scope=='work_order_only' and prompt_version=='rag-answer-v2':
                raise ValueError('WORK_ORDER_ONLY_NO_RAG_AUTHORIZATION')
            expected_format=(
                {'type':'json_object'} if prompt_version=='rag-answer-v2'
                else WORK_ORDER_RESPONSE_FORMAT if prompt_version==WORK_ORDER_PROMPT_VERSION
                else VISION_RESPONSE_FORMAT if prompt_version==VISION_ANALYSIS_PROMPT_VERSION
                else None
            )
            if expected_format is None:raise ValueError('PROMPT_VERSION_NOT_ALLOWED')
            if obj.get('response_format')!=expected_format:raise ValueError('QWEN_SCHEMA_MISMATCH')
            kind='qwen';value=obj
        else:raise ValueError('ENDPOINT_NOT_ALLOWED')
    if scope=='work_order_only' and kind in {'embedding','reranker'}:
        raise ValueError('WORK_ORDER_ONLY_ENDPOINT')
    validate(kind,value);return kind,value

def child(config_path,input_path,job_name):
    # Join from the actual interpreter, not the Windows venv launcher PID.
    # Missing/dead supervisor fails closed before importing/loading any model.
    join_request_job(job_name)
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));item=json.loads(Path(input_path).read_text(encoding='utf-8'))
    kind=item['kind'];value=base64.b64decode(item['value'],validate=True) if kind=='asr' else item['value']
    validate(kind,value)
    def emit(**data):print(json.dumps({'utc':stamp(),**data},ensure_ascii=False),flush=True)
    emit(phase='owned',actual_pid=os.getpid(),request_job=job_name)
    offline(Path(config['private'])/'hf-cache');start=time.monotonic();emit(phase='load')
    if kind=='qwen':
        emit(phase='inference',load_seconds=None)
        result=direct('/v1/chat/completions',value,timeout=60)
        emit(result=result,inference_seconds=time.monotonic()-start)
    else:
        model=load(kind,config['model_root']);emit(phase='inference',load_seconds=time.monotonic()-start)
        start=time.monotonic();result=infer(kind,model,value);emit(result=result,inference_seconds=time.monotonic()-start)

class Runner:
    def __init__(self,config_path):
        self.path=Path(config_path).resolve();self.config=json.loads(self.path.read_text(encoding='utf-8'))
        self.private=Path(self.config['private']);self.private.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock()
        self.state_lock=threading.Lock()
        self.accepting_requests=True
        # The launcher records and verifies pinned source/weights; runtime config
        # cannot widen invocation caps or choose arbitrary upstream endpoints.
        if self.config['host']!='127.0.0.1' or self.config['port'] not in {11436,11437}:raise ValueError('LOOPBACK_ONLY')
        self.scope=self.config.get('scope','all')
        if self.scope not in {'all','rag_only','work_order_only'}:raise ValueError('INVALID_SCOPE')
        if self.scope=='rag_only' and self.config['port']!=11436:raise ValueError('INVALID_SCOPE_PORT')
        if self.scope=='work_order_only' and self.config['port']!=11437:raise ValueError('INVALID_SCOPE_PORT')
        self.limits=self.config.get('limits',LIMITS)
        if set(self.limits)!=set(LIMITS) or any(
            not isinstance(self.limits[k],int) or not 0<self.limits[k]<=LIMITS[k]
            for k in LIMITS
        ):raise ValueError('INVALID_LIMITS')
        self.duration_seconds=self.config.get('duration_seconds',1800)
        if not isinstance(self.duration_seconds,int) or not 0<self.duration_seconds<=7200:
            raise ValueError('INVALID_DURATION')
        self.asr_start_ram_mib=self.config.get('asr_start_ram_mib',8192)
        if (
            not isinstance(self.asr_start_ram_mib,int)
            or not 8192<=self.asr_start_ram_mib<=32768
            or (self.scope=='work_order_only' and self.asr_start_ram_mib<11264)
        ):raise ValueError('ASR_ADMISSION_LIMIT')
        self.continuation=self.config.get('continuation')
        self.resolutions=list(self.config.get('resolutions',[]))
        self.runtime_binding=None
        binding_path=self.config.get('runtime_binding_path')
        if binding_path:
            binding=Path(binding_path).resolve();evidence_root=Path(self.config['evidence_root']).resolve()
            if not binding.is_relative_to(evidence_root):raise ValueError('RUNTIME_BINDING_SCOPE')
            if self._sha(binding)!=self.config.get('runtime_binding_sha256'):raise ValueError('RUNTIME_BINDING_DRIFT')
            self.runtime_binding=json.loads(binding.read_text(encoding='utf-8'))
        if self.scope=='work_order_only' and self.runtime_binding is None:
            raise ValueError('WORK_ORDER_RUNTIME_BINDING_REQUIRED')
        if self.scope=='work_order_only':self.verify_qwen_runtime_binding()
        if self.continuation:
            c=self.continuation
            if c.get('task_id')!='REAL-AI-INTEGRATION-027' or c.get('run_id')!='ai027-b439223fda':raise ValueError('CONTINUATION_SCOPE')
            if c.get('base_count')!=29 or not 0<c.get('duration_seconds',0)<=900:raise ValueError('CONTINUATION_LIMIT')
            if set(c.get('limits',{}))!=set(CONTINUATION_MAX) or any(not 0<c['limits'][k]<=CONTINUATION_MAX[k] for k in CONTINUATION_MAX):raise ValueError('CONTINUATION_LIMIT')
            if c.get('asr_start_ram_mib')!=11264:raise ValueError('ASR_ADMISSION_LIMIT')
            self.resolutions.extend(c.get('resolutions',[]))
    def acquire_request(self):
        with self.state_lock:
            if not self.accepting_requests:return False
            return self.lock.acquire(blocking=False)
    def begin_drain(self):
        with self.state_lock:
            self.accepting_requests=False
            return self.lock.locked()
    def admission(self):
        with self.state_lock:
            return {'accepting_requests':self.accepting_requests,'request_active':self.lock.locked()}
    @staticmethod
    def _sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    def verify_qwen_runtime_binding(self):
        if self.runtime_binding is None:raise RuntimeError('QWEN_RUNTIME_BINDING_REQUIRED')
        identity=bound_runtime_identity(self.runtime_binding)
        if loopback_listener_pid(11435)!=int(identity['pid']):
            raise RuntimeError('QWEN_RUNTIME_LISTENER_IDENTITY_CHANGED')
        return identity
    def _resolution(self,path,result):
        if not self.resolutions:return None
        for spec in self.resolutions:
            if spec.get('record')!=path.parent.name:continue
            report=Path(spec['resolution_path']).resolve();review=Path(spec['review_path']).resolve()
            allowed=Path(self.config.get('resolution_root',self.config['evidence_root'])).resolve()
            if not report.is_relative_to(allowed) or not review.is_relative_to(allowed):raise RuntimeError('RESOLUTION_PATH_SCOPE')
            reservation_hash=self._sha(path);result_hash=self._sha(path.parent/'result.json')
            if (reservation_hash.lower()!=spec['reservation_sha256'].lower()
                    or result_hash.lower()!=spec['result_sha256'].lower()):raise RuntimeError('RESOLUTION_ORIGINAL_DRIFT')
            if (self._sha(report).lower()!=spec['resolution_sha256'].lower()
                    or self._sha(review).lower()!=spec['review_sha256'].lower()):raise RuntimeError('RESOLUTION_REVIEW_DRIFT')
            data=json.loads(report.read_text(encoding='utf-8'));verdict=json.loads(review.read_text(encoding='utf-8'))
            if (data.get('original',{}).get('reservation_sha256','').lower()!=reservation_hash.lower()
                    or data.get('original',{}).get('result_sha256','').lower()!=result_hash.lower()):raise RuntimeError('RESOLUTION_ORIGINAL_DRIFT')
            original_status=result.get('status')
            if original_status not in {'FAILED','UNKNOWN'} or data.get('original',{}).get('status')!=original_status:raise RuntimeError('RESOLUTION_SEMANTICS')
            if data.get('record')!=path.parent.name or data.get('original',{}).get('historical_immediate_exit_confirmed')!='UNKNOWN':raise RuntimeError('RESOLUTION_SEMANTICS')
            if data.get('current_execution',{}).get('currently_executing') is not False:raise RuntimeError('RESOLUTION_SEMANTICS')
            if (verdict.get('verdict')!='PASS_SCOPED_CALL_RESOLUTION'
                    or verdict.get('resolution_sha256','').lower()!=spec['resolution_sha256'].lower()):raise RuntimeError('RESOLUTION_REVIEW_REQUIRED')
            return data
        return None
    def gate(self):
        prior=sorted(self.private.glob('call-*/reservation.json'))
        if any(not (p.parent/'result.json').exists() for p in prior):return 'BLOCKED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY'
        try:
            for p in prior:
                result=json.loads((p.parent/'result.json').read_text(encoding='utf-8'))
                resolution=self._resolution(p,result)
                if resolution:continue
                if result.get('status')=='SUCCESS':continue
                if result.get('status')=='UNKNOWN':return 'BLOCKED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY'
                if result.get('owned_exit_confirmed') is True:continue
                return 'BLOCKED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY'
        except (OSError,ValueError,KeyError,json.JSONDecodeError,RuntimeError):
            return 'BLOCKED_RESOLUTION_INVALID'
        return 'READY'
    def reserve(self,kind,input_hash=None):
        prior=sorted(self.private.glob('call-*/reservation.json'))
        records=[json.loads(p.read_text()) for p in prior]
        if self.gate()!='READY':
            raise RuntimeError('PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY')
        replay_of=None
        if input_hash:
            failed=[]
            for p,r in zip(prior,records):
                result=json.loads((p.parent/'result.json').read_text(encoding='utf-8'))
                if r.get('input_hash')==input_hash and r['kind']==kind and result.get('status')!='SUCCESS':failed.append((p,result))
            for p,result in failed:
                resolution=self._resolution(p,result)
                used=any(r.get('replay_of')==p.parent.name for r in records)
                if not resolution or not resolution.get('explicit_same_input_replay',{}).get('authorized') or used:
                    raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
                replay_of=p.parent.name
        now=time.time()
        if self.continuation:
            base=self.continuation['base_count'];new=records[base:]
            if len(records)<base:raise RuntimeError('CONTINUATION_BASE_MISSING')
            start=(new[0]['window_start_unix'] if new else now);deadline=start+self.continuation['duration_seconds']
            if now>=deadline:raise RuntimeError('R2_WINDOW_EXHAUSTED')
            if sum(r['kind']==kind for r in new)>=self.continuation['limits'][kind]:raise RuntimeError('R2_CALL_LIMIT')
            metadata={'window':'r2-continuation-01','window_start_unix':start,'incremental_max_calls':self.continuation['limits'][kind],'replay_of':replay_of}
        else:
            if records and now-records[0]['unix']>=self.duration_seconds:raise RuntimeError('R2_WINDOW_EXHAUSTED')
            if sum(r['kind']==kind for r in records)>=self.limits[kind]:raise RuntimeError('R2_CALL_LIMIT')
            deadline=(records[0]['unix'] if records else now)+self.duration_seconds;metadata={'replay_of':replay_of}
        folder=self.private/f'call-{len(records)+1:03d}';folder.mkdir()
        save(folder/'reservation.json',{'kind':kind,'unix':now,'utc':stamp(),'model':MODELS[kind],'max_calls':self.limits[kind],'input_hash':input_hash,**metadata})
        return folder,deadline
    def run(self,kind,value):
        validate(kind,value)
        if kind=='qwen':
            if self.scope=='work_order_only':self.verify_qwen_runtime_binding()
            qwen_guard()
        else:
            # Existing Qwen runtime unloads its own idle model after30s. Wait
            # serially, without a probe inference/retry or lowering the floor.
            started=time.monotonic();samples=[]
            minimum=self.asr_start_ram_mib if kind=='asr' else 8192
            while True:
                available=ram();samples.append(available)
                if available>=minimum:break
                if time.monotonic()-started>=35:
                    raise RuntimeError('CPU_START_RAM_GUARD')
                time.sleep(.25)
            if len(samples)>1:
                with (self.private/'resource-waits.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps({'utc':stamp(),'kind':kind,'seconds':time.monotonic()-started,'ram_samples_mib':samples,'inference_calls':0})+'\n')
        input_hash=hashlib.sha256(value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        folder,deadline=self.reserve(kind,input_hash)
        save(folder/'input.json',{'kind':kind,'value':base64.b64encode(value).decode() if kind=='asr' else value})
        environment='asr_python' if kind=='asr' else 'bge_python'
        job=RequestJob()
        command=[self.config[environment],'-I','-X','utf8',str(Path(__file__).resolve()),'child','--config',str(self.path),'--input',str(folder/'input.json'),'--job-name',job.name]
        samples=[];events=[];reason=None;start=time.monotonic();phase='load';phase_start=start
        stdout=folder/'stdout.jsonl';stderr=folder/'stderr.txt'
        p=None;owned_exit_confirmed=False;cleanup_error=None
        remote_stop=None;remote_stop_error=None
        try:
            with stdout.open('xb') as out,stderr.open('xb') as err:
                p=subprocess.Popen(command,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW,cwd=self.private)
                while p.poll() is None:
                    time.sleep(.25)
                    try:
                        point={'ram_free_mib':ram()}
                        if kind=='qwen':point['gpu_free_mib']=gpu()
                    except Exception:
                        reason='RESOURCE_OBSERVATION_FAILED';break
                    samples.append(point);events=[]
                    for line in stdout.read_text(encoding='utf-8').splitlines():
                        try:events.append(json.loads(line))
                        except ValueError:pass
                    if any(e.get('phase')=='inference' for e in events) and phase!='inference':phase='inference';phase_start=time.monotonic()
                    if point['ram_free_mib']<4500 or point.get('gpu_free_mib',2001)<2000:reason='RESOURCE_FLOOR'
                    elif time.time()>=deadline:reason='R2_WINDOW_EXHAUSTED'
                    elif time.monotonic()-phase_start>(120 if phase=='load' else 60):reason=phase.upper()+'_TIMEOUT'
                    if reason:break
        finally:
            if reason is None and p is not None and p.poll() is not None:
                if p.returncode!=0:
                    reason='REMOTE_CLIENT_FAILED'
                elif kind=='qwen':
                    observed=[]
                    for line in stdout.read_text(encoding='utf-8').splitlines():
                        try:observed.append(json.loads(line))
                        except ValueError:pass
                    if len([event for event in observed if 'result' in event])!=1:
                        reason='REMOTE_RESPONSE_INVALID'
            if kind=='qwen' and reason in {
                'RESOURCE_FLOOR','RESOURCE_OBSERVATION_FAILED','INFERENCE_TIMEOUT',
                'LOAD_TIMEOUT','R2_WINDOW_EXHAUSTED','REMOTE_CLIENT_FAILED',
                'REMOTE_RESPONSE_INVALID','DESCENDANTS_REMAINED_AFTER_LAUNCHER_EXIT',
            }:
                try:
                    if self.runtime_binding is None:
                        raise RuntimeError('QWEN_RUNTIME_BINDING_REQUIRED')
                    remote_stop=terminate_bound_runtime(
                        self.runtime_binding,listener_port=11435
                    )
                except Exception as exc:
                    remote_stop_error=type(exc).__name__
            try:
                if not reason and job.active():reason='DESCENDANTS_REMAINED_AFTER_LAUNCHER_EXIT'
                job.terminate_and_confirm()
                if p is not None:p.wait(timeout=10)
                owned_exit_confirmed=True
            except Exception as exc:cleanup_error=type(exc).__name__
            finally:job.close()
        events=[]
        for line in stdout.read_text(encoding='utf-8').splitlines():
            try:events.append(json.loads(line))
            except ValueError:pass
        results=[e['result'] for e in events if 'result' in e]
        success=p.returncode==0 and len(results)==1 and not reason and owned_exit_confirmed
        controlled_remote_stop=bool(
            (remote_stop or {}).get('stopped',{}).get('tree_exit_confirmed')
            and (remote_stop or {}).get('stopped',{}).get('listener_exit_confirmed')
        )
        # A failed Qwen request is known-complete only when both the owned HTTP
        # client and the receipt-bound remote runtime have demonstrably exited.
        unknown=(
            not owned_exit_confirmed
            or (kind=='qwen' and not success and not controlled_remote_stop)
        )
        save(folder/'result.json',{'status':'SUCCESS' if success else ('UNKNOWN' if unknown else 'FAILED'),'reason':reason,'exit_code':p.returncode,
            'model':MODELS[kind],'revision':DIGEST if kind=='qwen' else MODEL_DIRS[kind][1],
            'events':events,'samples':samples,'total_seconds':time.monotonic()-start,'child_pid':p.pid,
            'request_job':job.name,'owned_exit_confirmed':owned_exit_confirmed,'cleanup_error':cleanup_error,
            'remote_stop':remote_stop,'remote_stop_error':remote_stop_error,
            'remote_completion':'CONTROLLED_STOP' if controlled_remote_stop else (
                'UNKNOWN' if kind=='qwen' and not success else 'NOT_UNKNOWN'
            )})
        if not success:raise RuntimeError(reason or 'MODEL_REQUEST_FAILED_NO_RETRY')
        return results[0]

def serve(config_path):
    runner=Runner(config_path);token=os.environ.get('LOCAL_AI_INTERNAL_TOKEN','')
    if len(token)<32:raise RuntimeError('INTERNAL_AUTH_REQUIRED')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):super().setup();self.connection.settimeout(10)
        def reply(self,status,value):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorized(self):
            return self.client_address[0]=='127.0.0.1' and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            if not self.authorized():return self.reply(401,{'error':'AUTH_REQUIRED'})
            if self.path!='/healthz':return self.reply(404,{'error':'NOT_FOUND'})
            return self.reply(200,{'status':'ready','inference':False,'scope':runner.scope,'models':MODELS,'reservation_gate':runner.gate(),**runner.admission()})
        def do_POST(self):
            if not self.authorized():return self.reply(401,{'error':'AUTH_REQUIRED'})
            if self.path=='/admin/drain':
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length')!='0':
                    return self.reply(400,{'error':'EMPTY_BODY_REQUIRED'})
                active=runner.begin_drain()
                return self.reply(200,{'accepting_requests':False,'request_active':active})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:return self.reply(400,{'error':'CONTENT_LENGTH_REQUIRED'})
            try:
                size=int(self.headers['Content-Length'])
                if not 0<size<=MAX_BODY:return self.reply(413,{'error':'BODY_LIMIT'})
                body=self.rfile.read(size)
                if len(body)!=size:raise ValueError('INCOMPLETE_BODY')
                kind,value=decode(self.path,self.headers.get('Content-Type',''),body,self.headers.get('X-Prompt-Version',''),runner.scope)
            except (ValueError,TypeError,KeyError,UnicodeError):return self.reply(422,{'error':'INVALID_REQUEST'})
            if not runner.acquire_request():return self.reply(503,{'error':'MODEL_DRAINING_OR_BUSY_NO_RETRY'})
            try:return self.reply(200,runner.run(kind,value))
            except Exception:return self.reply(503,{'error':'MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY'})
            finally:runner.lock.release()
    server=ThreadingHTTPServer((runner.config['host'],runner.config['port']),Handler)
    print(json.dumps({'pid':os.getpid(),'listen':f"{runner.config['host']}:{runner.config['port']}",'python':sys.executable,'source':str(Path(__file__).resolve()),'utc':stamp(),'health_inference':False}),flush=True)
    server.serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['serve','child']);parser.add_argument('--config',required=True);parser.add_argument('--input');parser.add_argument('--job-name');args=parser.parse_args()
    if args.mode=='child':child(args.config,args.input,args.job_name)
    else:serve(args.config)
