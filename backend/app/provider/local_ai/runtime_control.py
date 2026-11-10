"""Process control, budget ledger, gates and prompt contracts for the adapter.

Moved verbatim out of scripts/ops/local_ai_http.py (package restructure phase 1).
"""
from datetime import UTC, datetime
import argparse
import base64
import ctypes
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
import math
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
import wave

# The reviewed sibling tools (local_ai_models / local_ai_process /
# local_ai_runtime_control) still live in scripts/ops; the adapter is also
# unpacked as <candidate>/backend/app/provider/local_ai/ + <candidate>/scripts/ops/.
_ADAPTER_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts" / "ops"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
for _entry in (str(_ADAPTER_SCRIPTS), str(_BACKEND_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
from local_ai_models import MODEL_DIRS, offline, load, infer
from local_ai_process import RequestJob, join_request_job
from local_ai_runtime_control import (
    bound_runtime_identity,
    loopback_listener_pid,
    terminate_bound_runtime,
)
from app.domain import ai as ai_contract
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.observed_event import work_order_analysis_response_format
from app.domain.vision import (
    VISION_ANALYSIS_PROMPT_VERSION,
    vision_analysis_response_format,
)

QWEN_CANDIDATES={
    'qwen3.5:4b':('2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd','0.34.4',4096),
    'qwen3.5:9b':('6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7','0.34.3',4096),
    'qwen38-27b-q3km-g55:7af3b8f':('eb18a122286f7a67d36dc8129f9caa1c0f7bb8449d7669a79e3ecb3d81289e0c','0.34.4',8192),
    'qwen38-27b-q3km-g55-ctx8192:7af3b8f':('06385258e076e9b22102da888a1593daef1dd1d679d863e5cc94c7c674ffed86','0.34.4',8192),
    'qwen38-27b-q3km-g52-ctx8192:7af3b8f':('59f3ff04c8d0909ab0f4f07d2977f71ed93c4e0c6c66240c91d6c4cfb8e82208','0.34.4',8192),
}
QWEN_MODEL=os.environ.get('PROPERTY_AI_LOCAL_QWEN_MODEL','qwen3.5:4b')
QWEN_RUNTIME_MODEL=os.environ.get('PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL',QWEN_MODEL)
if QWEN_MODEL not in QWEN_CANDIDATES or QWEN_MODEL.endswith('-ctx8192:7af3b8f'):
    raise RuntimeError('UNAPPROVED_QWEN_CANDIDATE')
if QWEN_RUNTIME_MODEL not in QWEN_CANDIDATES:raise RuntimeError('UNAPPROVED_QWEN_RUNTIME_MODEL')
QWEN_RUNTIME_BINDINGS=frozenset({
    ('qwen38-27b-q3km-g55:7af3b8f','qwen38-27b-q3km-g55-ctx8192:7af3b8f'),
    ('qwen38-27b-q3km-g55:7af3b8f','qwen38-27b-q3km-g52-ctx8192:7af3b8f'),
})
if QWEN_RUNTIME_MODEL!=QWEN_MODEL and (QWEN_MODEL,QWEN_RUNTIME_MODEL) not in QWEN_RUNTIME_BINDINGS:
    raise RuntimeError('UNAPPROVED_QWEN_RUNTIME_BINDING')
DIGEST,QWEN_RUNTIME_VERSION,QWEN_CONTEXT_LENGTH=QWEN_CANDIDATES[QWEN_RUNTIME_MODEL]
QWEN_ENDPOINT=os.environ.get('PROPERTY_AI_LOCAL_QWEN_ENDPOINT','http://127.0.0.1:11435')
if QWEN_ENDPOINT not in {'http://127.0.0.1:11435','http://127.0.0.1:11438'}:raise RuntimeError('UNAPPROVED_QWEN_ENDPOINT')
QWEN_INFERENCE_TIMEOUT_SECONDS=(
    180 if (QWEN_MODEL,QWEN_RUNTIME_MODEL) in QWEN_RUNTIME_BINDINGS else 60
)
# PA-130 (t130): the three resource floors were literals, so a host that misses
# the floor by a few dozen MiB could not say by how much, and could not be
# measured against any other value without editing this file. They stay
# observable and overridable through the environment, the defaults do not move,
# and a malformed value stops the adapter at import instead of silently
# admitting requests against a floor nobody chose.
FLOOR_ENV_VARS={
    'ram':'LOCAL_AI_RAM_FLOOR_MIB',
    'gpu':'LOCAL_AI_GPU_FLOOR_MIB',
    'cold_start_gpu':'LOCAL_AI_COLD_START_GPU_FLOOR_MIB',
}
FLOOR_DEFAULTS={'ram':4500,'gpu':2000,'cold_start_gpu':6000}
FLOOR_CEILING_MIB=262144
def floor_from_env(key):
    """Return (value, source) for one floor, or raise on an unusable value."""
    name=FLOOR_ENV_VARS[key];default=FLOOR_DEFAULTS[key]
    raw=os.environ.get(name)
    if raw is None or not raw.strip():return default,'default'
    try:value=int(raw.strip())
    except ValueError as exc:raise ValueError(f'INVALID_FLOOR_MIB:{name}:{raw.strip()}') from exc
    if not 0<value<=FLOOR_CEILING_MIB:raise ValueError(f'INVALID_FLOOR_MIB:{name}:{raw.strip()}')
    return value,f'env:{name}'
QWEN_RUNNING_RAM_FLOOR_MIB,QWEN_RUNNING_RAM_FLOOR_SOURCE=floor_from_env('ram')
QWEN_RUNNING_GPU_FLOOR_MIB,QWEN_RUNNING_GPU_FLOOR_SOURCE=floor_from_env('gpu')
QWEN_COLD_START_GPU_FLOOR_MIB,QWEN_COLD_START_GPU_FLOOR_SOURCE=floor_from_env('cold_start_gpu')
def effective_floors():
    return {
        'ram':{'floor_mib':QWEN_RUNNING_RAM_FLOOR_MIB,'source':QWEN_RUNNING_RAM_FLOOR_SOURCE},
        'gpu':{'floor_mib':QWEN_RUNNING_GPU_FLOOR_MIB,'source':QWEN_RUNNING_GPU_FLOOR_SOURCE},
        'cold_start_gpu':{'floor_mib':QWEN_COLD_START_GPU_FLOOR_MIB,'source':QWEN_COLD_START_GPU_FLOOR_SOURCE},
        'defaults':dict(FLOOR_DEFAULTS),
    }
def _observed(reader):
    try:return int(reader()),None
    except Exception as exc:return None,type(exc).__name__ or exc.__class__.__name__
def resource_floor(*,ram_mib=None,gpu_mib=None,cold_start=False,skip_gpu=False):
    """Report the floor that applies, what was measured, and the shortfall.

    ``short_by_mib`` is positive exactly when the measurement is below the
    floor, so an operator reads "38 MiB short" instead of a bare refusal, and
    the failing dimension is named rather than inferred.
    """
    if ram_mib is None:ram_mib,ram_error=_observed(ram)
    else:ram_error=None
    if skip_gpu:gpu_mib,gpu_error=None,None
    elif gpu_mib is None:gpu_mib,gpu_error=_observed(gpu)
    else:gpu_error=None
    gpu_floor=QWEN_COLD_START_GPU_FLOOR_MIB if cold_start else QWEN_RUNNING_GPU_FLOOR_MIB
    gpu_source=QWEN_COLD_START_GPU_FLOOR_SOURCE if cold_start else QWEN_RUNNING_GPU_FLOOR_SOURCE
    def bucket(floor,observed,source,error,skipped=False):
        if skipped:
            return {'floor_mib':floor,'observed_mib':None,'short_by_mib':None,'ok':True,'source':source,'observation_error':None,'skipped':True}
        short=None if observed is None else max(0,floor-observed)
        return {'floor_mib':floor,'observed_mib':observed,'short_by_mib':short,'ok':bool(short==0),'source':source,'observation_error':error}
    ram_bucket=bucket(QWEN_RUNNING_RAM_FLOOR_MIB,ram_mib,QWEN_RUNNING_RAM_FLOOR_SOURCE,ram_error)
    gpu_bucket=bucket(gpu_floor,gpu_mib,gpu_source,gpu_error,skipped=skip_gpu)
    shorts=[v['short_by_mib'] for v in (ram_bucket,gpu_bucket) if v['short_by_mib'] is not None]
    ok=bool(ram_bucket['ok'] and gpu_bucket['ok'])
    short_by=max(shorts) if shorts else None
    return {
        'ok':ok,'short_by_mib':short_by,'cold_start':bool(cold_start),
        'floor_mib':ram_bucket['floor_mib'] if not ram_bucket['ok'] else gpu_bucket['floor_mib'],
        'observed_mib':ram_bucket['observed_mib'] if not ram_bucket['ok'] else gpu_bucket['observed_mib'],
        'ram':ram_bucket,'gpu':gpu_bucket,'utc':stamp(),
    }
class ResourceFloorRefused(RuntimeError):
    """A floor refusal that keeps its original message and carries the numbers."""
    def __init__(self,snapshot,message='QWEN_RESOURCE_GUARD'):
        super().__init__(message)
        self.resource_floor=snapshot
MODELS={'asr':'Qwen3-ASR-0.6B','embedding':'bge-m3','reranker':'bge-reranker-v2-m3','qwen':QWEN_MODEL}
LIMITS={'asr':6,'embedding':12,'reranker':8,'qwen':120}
CONTINUATION_MAX={'asr':1,'embedding':6,'reranker':6,'qwen':6}
MAX_MEDIA_BYTES=10*1024*1024
# A 10 MiB image expands by 4/3 as a data URL. Keep a small fixed allowance
# for the prompt and JSON Schema while retaining an explicit request ceiling.
MAX_BODY=15*1024*1024
WORK_ORDER_PROMPT_VERSION=WORK_ORDER_ANALYSIS_PROMPT_VERSION
RAG_PROMPT_VERSIONS=frozenset({'rag-answer-v2','rag-answer-v3','rag-answer-v4'})
# PA-116 / PA-116-A-1: the shipped work-order prompt version must live in a
# versioned file, not only in whatever shell happened to launch this adapter.
# Resolution order, first hit wins:
#   1) PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION  explicit env override (CI / one-off)
#   2) PROPERTY_AI_PROMPT_VERSIONS_FILE         versioned JSON, ships as deploy/ai-runtime-prompts.json
#   3) the backend contract module              last-resort fallback, reported as such
def _prompt_versions_file():
    raw=os.environ.get('PROPERTY_AI_PROMPT_VERSIONS_FILE','').strip()
    if raw:return Path(raw)
    default=Path(__file__).resolve().parents[4]/'deploy'/'ai-runtime-prompts.json'
    return default if default.is_file() else None
def _prompt_versions_from_file(path):
    try:payload=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise RuntimeError('PROMPT_VERSIONS_FILE_INVALID') from exc
    if not isinstance(payload,dict) or payload.get('format')!='property-ai-runtime-prompts-v1':
        raise RuntimeError('PROMPT_VERSIONS_FILE_INVALID')
    values=payload.get('allowed_work_prompt_versions')
    if not isinstance(values,list) or not values:raise RuntimeError('PROMPT_VERSIONS_FILE_INVALID')
    if any(not isinstance(v,str) or not v.strip() for v in values):raise RuntimeError('PROMPT_VERSIONS_FILE_INVALID')
    if len(set(values))!=len(values):raise RuntimeError('PROMPT_VERSIONS_FILE_INVALID')
    return values
WORK_ORDER_PROMPT_VERSION_OVERRIDE=os.environ.get('PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION','').strip()
PROMPT_VERSIONS_FILE=_prompt_versions_file()
if WORK_ORDER_PROMPT_VERSION_OVERRIDE:
    WORK_ORDER_PROMPT_VERSIONS=[WORK_ORDER_PROMPT_VERSION_OVERRIDE]
    WORK_ORDER_PROMPT_VERSION_SOURCE='env:PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION'
elif PROMPT_VERSIONS_FILE:
    WORK_ORDER_PROMPT_VERSIONS=_prompt_versions_from_file(PROMPT_VERSIONS_FILE)
    WORK_ORDER_PROMPT_VERSION_SOURCE='file:'+str(PROMPT_VERSIONS_FILE)
else:
    WORK_ORDER_PROMPT_VERSIONS=[WORK_ORDER_PROMPT_VERSION]
    WORK_ORDER_PROMPT_VERSION_SOURCE='backend:'+str(Path(ai_contract.__file__))
WORK_ORDER_PROMPT_VERSION=WORK_ORDER_PROMPT_VERSIONS[0]
def allowed_prompt_versions():
    return {'work_order':list(WORK_ORDER_PROMPT_VERSIONS),'vision':[VISION_ANALYSIS_PROMPT_VERSION],'rag':sorted(RAG_PROMPT_VERSIONS),'source':WORK_ORDER_PROMPT_VERSION_SOURCE}
WORK_ORDER_RESPONSE_FORMAT=work_order_analysis_response_format()
VISION_RESPONSE_FORMAT=vision_analysis_response_format()
SAFE_PRE_RESERVATION_DETAILS=frozenset({
    'AUDIO_FIELDS','AUDIO_SIZE','BODY_LIMIT','CONTENT_LENGTH_REQUIRED',
    'EMBEDDING_INPUT','ENDPOINT_NOT_ALLOWED','INCOMPLETE_BODY','JSON_REQUIRED',
    'MODEL_NOT_ALLOWED','MODEL_OR_FIELDS','MULTIPART_REQUIRED','PCM_REQUIRED',
    'PROMPT_VERSION_NOT_ALLOWED','QWEN_INPUT','QWEN_SCHEMA_MISMATCH','QWEN_SETTINGS',
    'RAG_ONLY_NO_AUTO_AUTHORIZATION','RERANK_INPUT','TRUNCATED_AUDIO','VLM_INPUT',
    'WORK_ORDER_ONLY_ENDPOINT','WORK_ORDER_ONLY_NO_RAG_AUTHORIZATION',
})

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
    url=QWEN_ENDPOINT+path
    request=urllib.request.Request(url,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=timeout) as r:
        return json.loads(r.read(1_100_000))
# PA-131: a floor/timeout failure says something about THIS request, not about
# the shared 11435 runtime. Only the two "the runtime may have leaked" reasons
# may stop it; every other failure leaves it running so the next request meets
# the same resource guard instead of an identity error it cannot recover from.
RUNTIME_STOP_REASONS=frozenset({'REMOTE_CLIENT_FAILED','DESCENDANTS_REMAINED_AFTER_LAUNCHER_EXIT'})
RUNTIME_LEFT_RUNNING_REASONS=frozenset({
    'RESOURCE_FLOOR','RESOURCE_OBSERVATION_FAILED','INFERENCE_TIMEOUT','LOAD_TIMEOUT',
    'R2_WINDOW_EXHAUSTED','REMOTE_RESPONSE_INVALID',
})
def runtime_stop_required(reason):
    return reason in RUNTIME_STOP_REASONS
def request_completion_state(*,kind,reason,success,owned_exit_confirmed,controlled_remote_stop):
    """Decide whether a failed request left nothing of its own behind.

    A failed Qwen request used to be UNKNOWN unless it had also killed the
    shared runtime, which forced a shared barrier and blocked every later
    request. Refusing to stop a healthy runtime is a known state: the owned
    child is confirmed gone and the runtime is exactly as the launcher left it.
    """
    left_running=bool(
        kind=='qwen' and not success and owned_exit_confirmed
        and not controlled_remote_stop and reason in RUNTIME_LEFT_RUNNING_REASONS
    )
    unknown=bool(
        not owned_exit_confirmed
        or (kind=='qwen' and not success and not controlled_remote_stop and not left_running)
    )
    return left_running,unknown

def qwen_guard():
    if direct('/api/version').get('version')!=QWEN_RUNTIME_VERSION:raise RuntimeError('QWEN_VERSION_CHANGED')
    tags=direct('/api/tags')['models']
    if not any(x['name']==QWEN_RUNTIME_MODEL and x['digest']==DIGEST for x in tags):raise RuntimeError('QWEN_DIGEST_CHANGED')
    loaded=direct('/api/ps')['models']
    if len(loaded)>1 or any(x['name']!=QWEN_RUNTIME_MODEL or x['digest']!=DIGEST or x['context_length']!=QWEN_CONTEXT_LENGTH for x in loaded):raise RuntimeError('QWEN_RESIDENCY_CHANGED')
    # Once the reviewed runtime is already resident, use the same floor that
    # supervises an in-flight request. Requiring a larger second-call margin
    # here rejects the LLM stage immediately after a successful VLM stage even
    # though the first call remained above the approved running floor.
    snapshot=resource_floor(cold_start=not loaded)
    if not snapshot['ok']:
        # PA-130: keep the reason text (callers and unit tests match on it) and
        # carry the measurement alongside, so the refusal can say which
        # dimension is short and by how many MiB.
        raise ResourceFloorRefused(snapshot)

def inference_timeout_seconds(kind):
    return QWEN_INFERENCE_TIMEOUT_SECONDS if kind=='qwen' else 60

def validate(kind,value):
    if kind=='asr':
        if not isinstance(value,bytes) or len(value)>1_000_000:raise ValueError('AUDIO_SIZE')
        is_mp3=(value.startswith(b'ID3') or (
            len(value)>=2 and value[0]==0xff and value[1]&0xe0==0xe0
        ))
        if not is_mp3:
            try:
                with wave.open(io.BytesIO(value),'rb') as w:
                    if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getframerate()!=16000 or not 0<w.getnframes()<=480000 or w.getcomptype()!='NONE':raise ValueError('PCM_REQUIRED')
                    if len(w.readframes(w.getnframes()))!=w.getnframes()*2:raise ValueError('TRUNCATED_AUDIO')
            except (wave.Error,EOFError) as exc:raise ValueError('PCM_OR_MP3_REQUIRED') from exc
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
            try:image_bytes=base64.b64decode(image_url.split(';base64,',1)[1],validate=True)
            except (ValueError,base64.binascii.Error) as exc:raise ValueError('VLM_INPUT') from exc
            if not 0<len(image_bytes)<=MAX_MEDIA_BYTES:raise ValueError('VLM_INPUT')
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
            if scope=='rag_only' and prompt_version not in RAG_PROMPT_VERSIONS:
                raise ValueError('RAG_ONLY_NO_AUTO_AUTHORIZATION')
            if scope=='work_order_only' and prompt_version in RAG_PROMPT_VERSIONS:
                raise ValueError('WORK_ORDER_ONLY_NO_RAG_AUTHORIZATION')
            expected_format=(
                {'type':'json_object'} if prompt_version in RAG_PROMPT_VERSIONS
                else WORK_ORDER_RESPONSE_FORMAT if prompt_version in WORK_ORDER_PROMPT_VERSIONS
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
        emit(phase='inference',load_seconds=None,request_model=MODELS['qwen'],runtime_model=QWEN_RUNTIME_MODEL)
        runtime_value=value if QWEN_RUNTIME_MODEL==MODELS['qwen'] else {**value,'model':QWEN_RUNTIME_MODEL}
        result=direct('/v1/chat/completions',runtime_value,timeout=inference_timeout_seconds(kind))
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
        self.audit_lock=threading.Lock()
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
        self.call_baseline=self.config.get('call_baseline',{kind:0 for kind in LIMITS})
        if set(self.call_baseline)!=set(LIMITS) or any(
            isinstance(self.call_baseline[k],bool)
            or not isinstance(self.call_baseline[k],int)
            or not 0<=self.call_baseline[k]<=self.limits[k]
            for k in LIMITS
        ):raise ValueError('INVALID_CALL_BASELINE')
        self.duration_baseline_seconds=self.config.get('duration_baseline_seconds',0)
        if (
            isinstance(self.duration_baseline_seconds,bool)
            or not isinstance(self.duration_baseline_seconds,(int,float))
            or not math.isfinite(self.duration_baseline_seconds)
            or not 0<=self.duration_baseline_seconds<self.duration_seconds
        ):raise ValueError('INVALID_DURATION_BASELINE')
        self.asr_start_ram_mib=self.config.get('asr_start_ram_mib',8192)
        if (
            not isinstance(self.asr_start_ram_mib,int)
            or not 8192<=self.asr_start_ram_mib<=32768
            or (self.scope=='work_order_only' and self.asr_start_ram_mib<11264)
        ):raise ValueError('ASR_ADMISSION_LIMIT')
        self.shared_lock_path=Path(self.config.get('shared_model_lock_path',self.private.parent/'shared-model.lock')).resolve()
        self.shared_completion_barrier_path=self.shared_lock_path.with_name('completion-unknown.json')
        self.shared_lock_wait_seconds=self.config.get('shared_model_lock_wait_seconds',55)
        if (not isinstance(self.shared_lock_wait_seconds,int)
                or not 1<=self.shared_lock_wait_seconds<=55):raise ValueError('SHARED_MODEL_LOCK_LIMIT')
        self.continuation=self.config.get('continuation')
        self.resolutions=list(self.config.get('resolutions',[]))
        self.runtime_binding=None;self.runtime_binding_invalidated=False
        binding_path=self.config.get('runtime_binding_path')
        if binding_path:
            binding=Path(binding_path).resolve();evidence_root=Path(self.config['evidence_root']).resolve()
            if not binding.is_relative_to(evidence_root):raise ValueError('RUNTIME_BINDING_SCOPE')
            if self._sha(binding)!=self.config.get('runtime_binding_sha256'):raise ValueError('RUNTIME_BINDING_DRIFT')
            self.runtime_binding=json.loads(binding.read_text(encoding='utf-8'))
        self.require_runtime_binding=(self.scope=='work_order_only' or self.config.get('require_runtime_binding') is True)
        if self.require_runtime_binding and self.runtime_binding is None:
            raise ValueError('WORK_ORDER_RUNTIME_BINDING_REQUIRED' if self.scope=='work_order_only' else 'QWEN_RUNTIME_BINDING_REQUIRED')
        if self.require_runtime_binding:self.verify_qwen_runtime_binding()
        if self.continuation:
            c=self.continuation
            if c.get('task_id')!='REAL-AI-INTEGRATION-027' or c.get('run_id')!='ai027-b439223fda':raise ValueError('CONTINUATION_SCOPE')
            if c.get('base_count')!=29 or not 0<c.get('duration_seconds',0)<=900:raise ValueError('CONTINUATION_LIMIT')
            if set(c.get('limits',{}))!=set(CONTINUATION_MAX) or any(not 0<c['limits'][k]<=CONTINUATION_MAX[k] for k in CONTINUATION_MAX):raise ValueError('CONTINUATION_LIMIT')
            if c.get('asr_start_ram_mib')!=11264:raise ValueError('ASR_ADMISSION_LIMIT')
            self.resolutions.extend(c.get('resolutions',[]))
        # PA-166: reviewed evidence lives in the same private directory as live
        # reservations, so the cumulative budget used to double count it. Restoring
        # the reviewed call-* assets (the launcher refuses to start without them)
        # then spent the very budget the restore was meant to prove, and the two
        # numbers could not be told apart in the failure record either.
        # Budget counting now skips records declared as evidence; those stay on
        # disk with their pinned sha256 + review verdict and are accounted for by
        # call_baseline instead of the current window.
        declared=self.config.get('evidence_records',[])
        if not isinstance(declared,list) or any(not isinstance(x,str) or not x.strip() for x in declared):
            raise ValueError('INVALID_EVIDENCE_RECORDS')
        self.evidence_records={x.strip() for x in declared}
        self.evidence_records.update(
            spec['record'] for spec in self.resolutions if isinstance(spec.get('record'),str) and spec['record']
        )
    def record_runtime_stopped(self,*,reason,remote_stop):
        """Invalidate the in-memory binding and leave a receipt next to it.

        The launcher rebuilds the binding and clears ``runtime-binding*.json``
        on its next ``up``; this marker makes the stop auditable meanwhile and
        stops this process from re-verifying a pid it just killed.
        """
        self.runtime_binding_invalidated=True
        binding_path=self.config.get('runtime_binding_path')
        if not binding_path:return
        marker=Path(binding_path).with_suffix('.stopped.json')
        save(marker,{
            'format':'property-ai-runtime-stopped-v1','utc':stamp(),'reason':reason,
            'binding':str(Path(binding_path).resolve()),'remote_stop':remote_stop,
        })
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
        if self.runtime_binding_invalidated:
            raise RuntimeError('QWEN_RUNTIME_STOPPED_RESTART_REQUIRED')
        identity=bound_runtime_identity(self.runtime_binding)
        listener_port=int(QWEN_ENDPOINT.rsplit(':',1)[1])
        if loopback_listener_pid(listener_port)!=int(identity['pid']):
            raise RuntimeError('QWEN_RUNTIME_LISTENER_IDENTITY_CHANGED')
        return identity
    @staticmethod
    def _failure_code(exc):
        detail=str(exc)
        if isinstance(exc,(ValueError,TypeError,KeyError,UnicodeError)):
            return 'VALIDATION_REJECTED'
        if 'RUNTIME_STOPPED' in detail:
            # PA-131: the runtime was stopped on purpose after a dirty-runtime
            # failure, so the next request must say "restart" instead of
            # "internal error" (which is what a dead binding pid used to yield).
            return 'RUNTIME_STOPPED_RESTART_REQUIRED'
        if 'IDENTITY' in detail or 'LISTENER' in detail:
            return 'RUNTIME_IDENTITY_MISMATCH'
        if any(value in detail for value in ('QWEN_VERSION_CHANGED','QWEN_DIGEST_CHANGED','QWEN_RESIDENCY_CHANGED')):
            return 'MODEL_RESIDENCY_MISMATCH'
        if any(value in detail for value in ('RESOURCE','RAM_GUARD','SHARED_MODEL_LOCK')):
            return 'RESOURCE_ADMISSION_REJECTED'
        if 'FAILED_INPUT' in detail:
            # PA-169: a quarantined input hash is a model-output verdict, not a
            # budget refusal; reporting it as BUDGET_REJECTED hid the retry path.
            return 'INPUT_QUARANTINED'
        if 'PREVIOUS_REQUEST' in detail:
            # PA-169/F3: "the previous request's completion is unknown" is not a
            # spent budget. Folding it into BUDGET_REJECTED sent the operator to
            # the wrong counter, exactly like the quarantine code used to.
            return 'PREVIOUS_REQUEST_UNKNOWN'
        if 'REMOTE_RESPONSE_INVALID' in detail:
            # PA-169/F2: a child that exits without one result line produced no
            # usable answer; say so instead of "internal error".
            return 'OUTPUT_UNVERIFIABLE'
        if any(value in detail for value in ('CALL_LIMIT','WINDOW_EXHAUSTED','BUDGET')):
            return 'BUDGET_REJECTED'
        if isinstance(exc,(TimeoutError,ConnectionError,OSError)):
            return 'REMOTE_UNAVAILABLE'
        return 'INTERNAL_PRE_RESERVATION_ERROR'
    def record_pre_reservation_failure(self,exc,*,stage,kind=None,input_hash=None,request_binding=None,prompt_version=None):
        reason=self._failure_code(exc)
        detail=str(exc) if str(exc) in SAFE_PRE_RESERVATION_DETAILS else None
        record={
            'utc':stamp(),'stage':stage,'reason_code':reason,'scope':self.scope,
            'detail_code':detail,
            'kind':kind,'model':MODELS.get(kind),'model_digest':DIGEST if kind=='qwen' else None,
            'runtime_version':QWEN_RUNTIME_VERSION if kind=='qwen' else None,
            'context_length':QWEN_CONTEXT_LENGTH if kind=='qwen' else None,
            'input_sha256':input_hash,'request_binding_sha256':request_binding,
            'prompt_version':prompt_version,'allowed_prompt_versions':allowed_prompt_versions(),
            'reservation_created':False,'fail_closed':True,
            # PA-130: a resource refusal carries the numbers beside the code, so
            # "rejected" never has to be read as "the machine is broken".
            'resource_floor':getattr(exc,'resource_floor',None),
        }
        self.private.mkdir(parents=True,exist_ok=True)
        with self.audit_lock,(self.private/'pre-reservation-failures.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
        return reason
    def acquire_shared_model_lock(self):
        self.shared_lock_path.parent.mkdir(parents=True,exist_ok=True)
        handle=self.shared_lock_path.open('a+b');handle.seek(0)
        if handle.tell()==0:handle.write(b'0');handle.flush()
        deadline=time.monotonic()+self.shared_lock_wait_seconds
        while True:
            try:
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);return handle
            except OSError:
                if time.monotonic()>=deadline:
                    handle.close();raise RuntimeError('SHARED_MODEL_LOCK_TIMEOUT')
                time.sleep(.1)
    @staticmethod
    def release_shared_model_lock(handle):
        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1);handle.close()
    def assert_shared_completion_ready(self):
        if self.shared_completion_barrier_path.exists():
            raise RuntimeError('SHARED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN')
    def record_shared_completion_unknown(self,folder,result):
        # PA-132: the barrier explains itself. Before this, an operator facing "everything is
        # blocked" had to read this source to learn which call caused it, what it touches, and
        # what the sanctioned way out is (there was none besides deleting a file by hand).
        # These fields are additive: gate() still keys off the file's existence, so the
        # fail-closed semantics are unchanged.
        record={
            'format':'property-ai-shared-model-completion-barrier-v1',
            'utc':stamp(),'scope':self.scope,'record':folder.name,
            'result_sha256':self._sha(folder/'result.json'),
            'status':result['status'],'reason':result.get('reason'),
            'owned_exit_confirmed':result.get('owned_exit_confirmed'),
            'remote_completion':result.get('remote_completion'),
            'call_id':folder.name,
            'affected_scopes':[self.scope,'rag'] if self.scope!='rag' else ['rag','work_order_only'],
            'gate':'BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY',
            'remediation':{
                'command':f'deploy/ai-runtime.ps1 resolve-unknown -Call {folder.name} -Reason "<why releasing is safe>"',
                'effect':'archives this barrier under resolved/ with an operator receipt; no restart needed',
                'requires_restart':False,
                'replay_authorized':False,
            },
        }
        self.shared_completion_barrier_path.parent.mkdir(parents=True,exist_ok=True)
        try:
            with self.shared_completion_barrier_path.open('x',encoding='utf-8') as f:
                json.dump(record,f,ensure_ascii=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
        except FileExistsError:
            raise RuntimeError('SHARED_PREVIOUS_REQUEST_COMPLETION_UNKNOWN')
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
    def reservation_paths(self):
        return sorted(self.private.glob('call-*/reservation.json'))
    def budget_ledger(self):
        """PA-166: split reviewed evidence from live spend.

        ``counted`` reservations consume the cumulative budget; ``evidence``
        reservations are historical assets that must stay on disk (the launcher
        and reviewers read them) but are accounted for by ``call_baseline``.
        """
        counted=[];evidence=[]
        for path in self.reservation_paths():
            (evidence if path.parent.name in self.evidence_records else counted).append(path)
        return {'counted':counted,'evidence':evidence}
    def budget_usage(self):
        ledger=self.budget_ledger()
        kinds={kind:0 for kind in LIMITS}
        for path in ledger['counted']:
            kinds[json.loads(path.read_text(encoding='utf-8'))['kind']]+=1
        return {
            'limits':dict(self.limits),'call_baseline':dict(self.call_baseline),
            'counted':kinds,'counted_records':[p.parent.name for p in ledger['counted']],
            'evidence_excluded':len(ledger['evidence']),
            'evidence_records':[p.parent.name for p in ledger['evidence']],
        }
    def next_call_index(self):
        used=[]
        for path in self.private.glob('call-*'):
            try:used.append(int(path.name.split('-',1)[1]))
            except (IndexError,ValueError):continue
        return max(used,default=0)+1
    def gate(self):
        if self.shared_completion_barrier_path.exists():
            return 'BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY'
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
    def reserve(self,kind,input_hash=None,force_retry=None):
        prior=sorted(self.private.glob('call-*/reservation.json'))
        records=[json.loads(p.read_text()) for p in prior]
        # PA-166: reviewed evidence is present on disk but is not current spend.
        ledger=self.budget_ledger()
        counted_records=[json.loads(p.read_text()) for p in ledger['counted']]
        if self.gate()!='READY':
            raise RuntimeError('PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY')
        # PA-169: only a run in which the model actually answered badly may keep
        # an input hash quarantined. A runtime-side failure (resource floor,
        # timeout, reservation refused) never reached the model, so the same
        # words stay submittable - pinning them forever turned one temporary
        # hiccup into a permanent 503 that still claimed BUDGET_REJECTED.
        replay_of=None;forced=None;retried=[];quarantined=[]
        if input_hash:
            failed=[]
            for p,r in zip(prior,records):
                result=json.loads((p.parent/'result.json').read_text(encoding='utf-8'))
                if r.get('input_hash')==input_hash and r['kind']==kind and result.get('status')!='SUCCESS':failed.append((p,result))
            for p,result in failed:
                origin=failure_origin(result.get('reason'))
                # PA-169/F1: this layer can only judge what the adapter itself
                # concluded, and today it judges transport, not the nine-field
                # answer - so no MODEL_OUTPUT_* (or REMOTE_RESPONSE_INVALID) row
                # is ever produced in production. The branch is reserved for an
                # adapter that does judge output; live isolation runs in
                # app/service/analysis against AI.INVALID_OUTPUT rows on the same
                # input_hash. Deleting it would silently unlearn the rule.
                if origin==FAILURE_ORIGIN_MODEL_OUTPUT:quarantined.append((p,result))
                else:retried.append({'record':p.parent.name,'reason':result.get('reason'),'origin':origin})
            if quarantined and force_retry:
                # The operator asked for this exact text anyway; the reservation
                # records who asked, so the replay is auditable rather than silent.
                forced={'actor':force_retry.get('actor'),'utc':stamp(),
                        'request_binding':force_retry.get('request_binding'),
                        'quarantined':[p.parent.name for p,_ in quarantined]}
            else:
                for index,(p,result) in enumerate(quarantined):
                    resolution=self._resolution(p,result)
                    children=[r for r in records if r.get('replay_of')==p.parent.name]
                    if not resolution or not resolution.get('explicit_same_input_replay',{}).get('authorized'):
                        raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
                    if index<len(quarantined)-1:
                        expected_child=quarantined[index+1][0].parent.name
                        if len(children)!=1 or children[0].get('input_hash')!=input_hash or children[0].get('kind')!=kind:
                            raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
                        child_path=prior[records.index(children[0])]
                        if child_path.parent.name!=expected_child:
                            raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
                    else:
                        if children:raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
                        replay_of=p.parent.name
        retry_metadata={}
        if retried:retry_metadata['retried_failures']=retried
        if forced:retry_metadata['forced_retry']=forced
        now=time.time()
        if self.continuation:
            base=self.continuation['base_count'];new=records[base:]
            if len(records)<base:raise RuntimeError('CONTINUATION_BASE_MISSING')
            start=(new[0]['window_start_unix'] if new else now);deadline=start+self.continuation['duration_seconds']
            if now>=deadline:raise RuntimeError('R2_WINDOW_EXHAUSTED')
            if sum(r['kind']==kind for r in new)>=self.continuation['limits'][kind]:raise RuntimeError('R2_CALL_LIMIT')
            metadata={'window':'r2-continuation-01','window_start_unix':start,'incremental_max_calls':self.continuation['limits'][kind],'replay_of':replay_of,**retry_metadata}
        else:
            if self.call_baseline[kind]+sum(r['kind']==kind for r in counted_records)>=self.limits[kind]:
                raise RuntimeError('R2_CALL_LIMIT')
            spent=float(self.duration_baseline_seconds)
            for path in ledger['counted']:
                result=json.loads((path.parent/'result.json').read_text(encoding='utf-8'))
                elapsed=result.get('total_seconds')
                if isinstance(elapsed,bool) or not isinstance(elapsed,(int,float)) or not math.isfinite(elapsed) or elapsed<0:
                    raise RuntimeError('R2_BUDGET_RECORD_INVALID')
                spent+=float(elapsed)
            remaining=self.duration_seconds-spent
            if remaining<=0:raise RuntimeError('R2_WINDOW_EXHAUSTED')
            deadline=now+remaining;metadata={
                'replay_of':replay_of,'budget':'cumulative-model-runtime-v1',
                'call_baseline':self.call_baseline[kind],
                'cumulative_seconds_before':spent,
                'budget_counted':True,
                'evidence_excluded_before':len(ledger['evidence']),
                **retry_metadata,
            }
        folder=self.private/f'call-{self.next_call_index():03d}';folder.mkdir()
        save(folder/'reservation.json',{'kind':kind,'unix':now,'utc':stamp(),'model':MODELS[kind],'max_calls':self.limits[kind],'input_hash':input_hash,**metadata})
        return folder,deadline
    def run(self,kind,value,request_binding=None,force_retry=None):
        input_hash=hashlib.sha256(value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        stage='VALIDATION'
        try:
            validate(kind,value)
            if kind=='qwen':
                stage='RUNTIME_IDENTITY'
                if self.require_runtime_binding:self.verify_qwen_runtime_binding()
                stage='MODEL_RESIDENCY_AND_RESOURCE'
                qwen_guard()
        except Exception as exc:
            self.record_pre_reservation_failure(exc,stage=stage,kind=kind,input_hash=input_hash,request_binding=request_binding)
            raise
        if kind!='qwen':
            # Existing Qwen runtime unloads its own idle model after30s. Wait
            # serially, without a probe inference/retry or lowering the floor.
            started=time.monotonic();samples=[]
            minimum=self.asr_start_ram_mib if kind=='asr' else 8192
            while True:
                available=ram();samples.append(available)
                if available>=minimum:break
                if time.monotonic()-started>=35:
                    exc=RuntimeError('CPU_START_RAM_GUARD')
                    self.record_pre_reservation_failure(exc,stage='RESOURCE_ADMISSION',kind=kind,input_hash=input_hash,request_binding=request_binding)
                    raise exc
                time.sleep(.25)
            if len(samples)>1:
                with (self.private/'resource-waits.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps({'utc':stamp(),'kind':kind,'seconds':time.monotonic()-started,'ram_samples_mib':samples,'inference_calls':0})+'\n')
        try:folder,deadline=self.reserve(kind,input_hash,force_retry=force_retry)
        except Exception as exc:
            self.record_pre_reservation_failure(exc,stage='BUDGET_AND_LEDGER',kind=kind,input_hash=input_hash,request_binding=request_binding)
            raise
        save(folder/'input.json',{'kind':kind,'value':base64.b64encode(value).decode() if kind=='asr' else value})
        environment='asr_python' if kind=='asr' else 'bge_python'
        job=RequestJob()
        command=[self.config[environment],'-I','-B','-X','utf8',str(Path(__file__).resolve()),'child','--config',str(self.path),'--input',str(folder/'input.json'),'--job-name',job.name]
        samples=[];events=[];reason=None;start=time.monotonic();phase='load';phase_start=start
        floor_snapshot=None
        stdout=folder/'stdout.jsonl';stderr=folder/'stderr.txt'
        p=None;owned_exit_confirmed=False;cleanup_error=None
        remote_stop=None;remote_stop_error=None;runtime_disposition='UNTOUCHED'
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
                    floor_snapshot=resource_floor(
                        ram_mib=point['ram_free_mib'],
                        gpu_mib=point.get('gpu_free_mib'),
                        skip_gpu=kind!='qwen',
                    )
                    if not floor_snapshot['ok']:reason='RESOURCE_FLOOR'
                    elif time.time()>=deadline:reason='R2_WINDOW_EXHAUSTED'
                    elif time.monotonic()-phase_start>(120 if phase=='load' else inference_timeout_seconds(kind)):reason=phase.upper()+'_TIMEOUT'
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
            if kind=='qwen' and runtime_stop_required(reason):
                try:
                    if self.runtime_binding is None:
                        raise RuntimeError('QWEN_RUNTIME_BINDING_REQUIRED')
                    remote_stop=terminate_bound_runtime(
                        self.runtime_binding,listener_port=int(QWEN_ENDPOINT.rsplit(':',1)[1])
                    )
                    runtime_disposition='STOPPED_DIRTY_RUNTIME'
                    self.record_runtime_stopped(reason=reason,remote_stop=remote_stop)
                except Exception as exc:
                    remote_stop_error=type(exc).__name__
            elif kind=='qwen' and reason in RUNTIME_LEFT_RUNNING_REASONS:
                runtime_disposition='LEFT_RUNNING'
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
        left_running,unknown=request_completion_state(
            kind=kind,reason=reason,success=success,
            owned_exit_confirmed=owned_exit_confirmed,
            controlled_remote_stop=controlled_remote_stop,
        )
        if left_running:runtime_disposition='LEFT_RUNNING'
        result={'status':'SUCCESS' if success else ('UNKNOWN' if unknown else 'FAILED'),'reason':reason,'exit_code':p.returncode,
            # PA-169: the ledger records how far the request got, so a later
            # retry of the same input hash can tell "the model answered badly"
            # apart from "the machine never ran it".
            'failure_origin':None if success else failure_origin(reason),
            'model':MODELS[kind],'revision':DIGEST if kind=='qwen' else MODEL_DIRS[kind][1],
            'events':events,'samples':samples,'total_seconds':time.monotonic()-start,'child_pid':p.pid,
            'request_job':job.name,'owned_exit_confirmed':owned_exit_confirmed,'cleanup_error':cleanup_error,
            'remote_stop':remote_stop,'remote_stop_error':remote_stop_error,
            'runtime_disposition':runtime_disposition,
            'resource_floor':floor_snapshot,
            'remote_completion':('CONTROLLED_STOP' if controlled_remote_stop
                else 'LEFT_RUNNING' if left_running
                else 'UNKNOWN' if unknown
                else 'NOT_UNKNOWN')}
        save(folder/'result.json',result)
        if unknown:self.record_shared_completion_unknown(folder,result)
        if not success:
            if reason=='RESOURCE_FLOOR' and floor_snapshot is not None:
                raise ResourceFloorRefused(floor_snapshot,'RESOURCE_FLOOR')
            raise RuntimeError(reason or 'MODEL_REQUEST_FAILED_NO_RETRY')
        return results[0]

# PA-169: which failures mean "the model answered and we rejected the answer".
# Everything else (resource floor, observation failure, timeout, remote
# incompleteness, reservation refusals) never reached the model, so it must not
# quarantine the input hash - the same words have to be submittable again once
# the machine recovers.
MODEL_OUTPUT_FAILURE_REASONS=frozenset({
    'MODEL_OUTPUT_REJECTED',      # the answer failed the nine-field contract
    'MODEL_OUTPUT_INVALID',       # the answer was not parseable
    'MODEL_OUTPUT_SCHEMA_INVALID',# the answer shape was wrong
    # PA-169/F2: the child ran to completion but emitted no single result line.
    # Nothing about that says "the machine was busy", so it counts as an unusable
    # answer and takes the quarantine path instead of the auto-retry path.
    'REMOTE_RESPONSE_INVALID',
})
FAILURE_ORIGIN_RUNTIME='runtime'
FAILURE_ORIGIN_MODEL_OUTPUT='model_output'
def failure_origin(reason):
    """Classify a failed call by how far the request got (PA-169)."""
    return FAILURE_ORIGIN_MODEL_OUTPUT if reason in MODEL_OUTPUT_FAILURE_REASONS else FAILURE_ORIGIN_RUNTIME

def failure_body(exc):
    """The 503 body: the same error string, plus numbers only when refused on
    resources (PA-130). Readiness codes and status codes are untouched."""
    body={'error':'MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY'}
    snapshot=getattr(exc,'resource_floor',None)
    if snapshot is not None:body['resource_floor']=snapshot
    # PA-169/F3: every refusal now carries the same code the ledger records.
    # Only one of the nine reasons used to be visible, so eight of them arrived
    # at the caller as an indistinguishable 503.
    body['reason_code']=Runner._failure_code(exc)
    return body

# 子进程入口。包化之前 Runner.run 用 Path(__file__) 把自己当脚本再拉起一次，
# 那段 __main__ 分发留在 scripts/ops/local_ai_http.py 里；文件搬进包之后这一份
# 才是 Runner.run 真正 spawn 的文件，所以 child 模式必须在这里可达，否则子进程
# 空跑退出、prompt 请求统一报 REMOTE_RESPONSE_INVALID（t138 实测）。
if __name__ == '__main__':
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(prog='local_ai_runtime_control')
    _parser.add_argument('mode', choices=['child'])
    _parser.add_argument('--config', required=True)
    _parser.add_argument('--input')
    _parser.add_argument('--job-name')
    _args = _parser.parse_args()
    child(_args.config, _args.input, _args.job_name)
