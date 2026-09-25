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
from local_ai_models import MODEL_DIRS,offline,load,infer
from local_ai_process import RequestJob,join_request_job

MODELS={'asr':'Qwen3-ASR-0.6B','embedding':'bge-m3','reranker':'bge-reranker-v2-m3','qwen':'qwen3.5:4b'}
LIMITS={'asr':1,'embedding':24,'reranker':18,'qwen':18}
MAX_BODY=1_100_000
DIGEST='2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd'

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
    if direct('/api/version').get('version')!='0.34.2':raise RuntimeError('QWEN_VERSION_CHANGED')
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
        if not isinstance(value,dict) or set(value)!=expected or value['model']!=MODELS['qwen'] or value['temperature']!=0 or value['reasoning_effort']!='none' or value['max_tokens']!=512 or value['response_format']!={'type':'json_object'}:raise ValueError('QWEN_SETTINGS')
        messages=value['messages']
        if not isinstance(messages,list) or len(messages)!=1 or set(messages[0])!={'role','content'} or messages[0]['role']!='user' or not isinstance(messages[0]['content'],str) or not 0<len(messages[0]['content'])<=10000:raise ValueError('QWEN_INPUT')

def decode(path,content_type,body,prompt_version):
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
            # R3 did NOT pass. This server cannot authorize work-order inference.
            if prompt_version!='rag-answer-v2':raise ValueError('RAG_ONLY_NO_AUTO_AUTHORIZATION')
            kind='qwen';value=obj
        else:raise ValueError('ENDPOINT_NOT_ALLOWED')
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
        # The launcher records and verifies pinned source/weights; runtime config
        # cannot widen invocation caps or choose arbitrary upstream endpoints.
        if self.config['host']!='127.0.0.1' or self.config['port']!=11436:raise ValueError('LOOPBACK_ONLY')
    def reserve(self,kind,input_hash=None):
        prior=sorted(self.private.glob('call-*/reservation.json'))
        records=[json.loads(p.read_text()) for p in prior]
        if any(not (p.parent/'result.json').exists() for p in prior):
            raise RuntimeError('PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY')
        results=[json.loads(p.parent.joinpath('result.json').read_text(encoding='utf-8')) for p in prior]
        if any(r.get('status')=='UNKNOWN' or (r.get('status')!='SUCCESS' and r.get('owned_exit_confirmed') is not True) for r in results):
            raise RuntimeError('PREVIOUS_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY')
        if input_hash and any(r.get('input_hash')==input_hash and r['kind']==kind and
            json.loads(p.parent.joinpath('result.json').read_text())['status']!='SUCCESS'
            for p,r in zip(prior,records)):
            raise RuntimeError('FAILED_INPUT_NO_AUTOMATIC_MODEL_RETRY')
        now=time.time()
        if records and now-records[0]['unix']>=1800:raise RuntimeError('R2_WINDOW_EXHAUSTED')
        if sum(r['kind']==kind for r in records)>=LIMITS[kind]:raise RuntimeError('R2_CALL_LIMIT')
        deadline=(records[0]['unix'] if records else now)+1800
        folder=self.private/f'call-{len(records)+1:03d}';folder.mkdir()
        save(folder/'reservation.json',{'kind':kind,'unix':now,'utc':stamp(),'model':MODELS[kind],'max_calls':LIMITS[kind],'input_hash':input_hash})
        return folder,deadline
    def run(self,kind,value):
        validate(kind,value)
        if kind=='qwen':qwen_guard()
        else:
            # Existing Qwen runtime unloads its own idle model after30s. Wait
            # serially, without a probe inference/retry or lowering the floor.
            started=time.monotonic();samples=[]
            while True:
                available=ram();samples.append(available)
                if available>=8192:break
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
                    if point['ram_free_mib']<(2000 if kind=='qwen' else 4500) or point.get('gpu_free_mib',2001)<2000:reason='RESOURCE_FLOOR'
                    elif time.time()>=deadline:reason='R2_WINDOW_EXHAUSTED'
                    elif time.monotonic()-phase_start>(120 if phase=='load' else 60):reason=phase.upper()+'_TIMEOUT'
                    if reason:break
        finally:
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
        # Local HTTP-client exit cannot establish that remote Ollama stopped.
        unknown=not owned_exit_confirmed or (kind=='qwen' and not success)
        save(folder/'result.json',{'status':'SUCCESS' if success else ('UNKNOWN' if unknown else 'FAILED'),'reason':reason,'exit_code':p.returncode,
            'model':MODELS[kind],'revision':DIGEST if kind=='qwen' else MODEL_DIRS[kind][1],
            'events':events,'samples':samples,'total_seconds':time.monotonic()-start,'child_pid':p.pid,
            'request_job':job.name,'owned_exit_confirmed':owned_exit_confirmed,'cleanup_error':cleanup_error,
            'remote_completion':'UNKNOWN' if kind=='qwen' and not success else 'NOT_UNKNOWN'})
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
            return self.reply(200,{'status':'ready','inference':False,'scope':'R2_ONLY','models':MODELS})
        def do_POST(self):
            if not self.authorized():return self.reply(401,{'error':'AUTH_REQUIRED'})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:return self.reply(400,{'error':'CONTENT_LENGTH_REQUIRED'})
            try:
                size=int(self.headers['Content-Length'])
                if not 0<size<=MAX_BODY:return self.reply(413,{'error':'BODY_LIMIT'})
                body=self.rfile.read(size)
                if len(body)!=size:raise ValueError('INCOMPLETE_BODY')
                kind,value=decode(self.path,self.headers.get('Content-Type',''),body,self.headers.get('X-Prompt-Version',''))
            except (ValueError,TypeError,KeyError,UnicodeError):return self.reply(422,{'error':'INVALID_REQUEST'})
            if not runner.lock.acquire(blocking=False):return self.reply(503,{'error':'SERIAL_MODEL_BUSY_NO_RETRY'})
            try:return self.reply(200,runner.run(kind,value))
            except Exception:return self.reply(503,{'error':'MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY'})
            finally:runner.lock.release()
    server=ThreadingHTTPServer((runner.config['host'],runner.config['port']),Handler)
    print(json.dumps({'pid':os.getpid(),'listen':'127.0.0.1:11436','python':sys.executable,'source':str(Path(__file__).resolve()),'utc':stamp(),'health_inference':False}),flush=True)
    server.serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['serve','child']);parser.add_argument('--config',required=True);parser.add_argument('--input');parser.add_argument('--job-name');args=parser.parse_args()
    if args.mode=='child':child(args.config,args.input,args.job_name)
    else:serve(args.config)
