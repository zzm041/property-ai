"""R1 exact native checks and persistent three-request /15min compatibility budget."""
from datetime import UTC,datetime
import argparse
import ctypes
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent/'compatibility'
PRIVATE=ROOT/'tmp/selfhost-ai-runtime/compat-027'
MODELS=ROOT/'tmp/selfhost-ai-local/models-hf'
sys.path.insert(0,str(ROOT/'scripts/ops'))
from local_ai_models import offline,load,infer

def write(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def ram():
    class Mem(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(x,ctypes.c_ulonglong) for x in ('total','available','page','available_page','virtual','available_virtual','extended')]
    status=Mem();status.length=ctypes.sizeof(status)
    assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.available//(1024*1024)
def emit(**v):print(json.dumps({'utc':datetime.now(UTC).isoformat(),**v}),flush=True)
def python_for(kind):return ROOT/'tmp/selfhost-ai-runtime'/('asr-006-cpu' if kind=='asr' else 'bge-142-cpu')/'.venv/Scripts/python.exe'

def child(kind,native):
    offline(PRIVATE/'hf-cache');emit(phase='native' if native else 'load')
    assert Path(sys.executable).resolve()==python_for(kind).resolve()
    import torch, transformers, numpy, scipy, tokenizers
    assert torch.__version__=='2.9.1+cpu' and torch.version.cuda is None
    if native:
        if kind=='asr':import qwen_asr, soundfile
        else:import FlagEmbedding
        emit(result={'python':sys.executable,'version':sys.version,'torch':torch.__version__,'native':torch._C.__file__,
            'numpy':numpy.__file__,'transformers':transformers.__file__,'packages':{n:metadata.version(n) for n in ('torch','transformers','numpy','scipy','tokenizers',*(['qwen-asr','soundfile'] if kind=='asr' else ['FlagEmbedding']))}})
        return
    started=time.monotonic();model=load(kind,MODELS);emit(phase='inference',load_seconds=time.monotonic()-started)
    if kind=='asr':
        ref=json.loads((OUT.parent/'audio-reference.json').read_text(encoding='utf-8'))
        audio=Path(ref['path']);assert hashlib.sha256(audio.read_bytes()).hexdigest().upper()==ref['sha256']
        with wave.open(str(audio),'rb') as w:assert 0<w.getnframes()/w.getframerate()<=15
        value=audio.read_bytes()
    elif kind=='embedding':value=['厨房水龙头持续滴水','停车场照明灯不亮']
    else:value={'query':'水龙头滴水如何报修','documents':['进入报修页面描述水龙头滴水并填写本次联系电话。','本条介绍小区公告的审核历史。']}
    started=time.monotonic();result=infer(kind,model,value);emit(result=result,inference_seconds=time.monotonic()-started)

def run(kind,native=False,adapt_native=False):
    OUT.mkdir(exist_ok=True);PRIVATE.mkdir(exist_ok=True)
    label=f'native-{kind}' if native else kind
    if adapt_native:
        assert native and kind=='embedding'
        prior=json.loads((OUT/'native-embedding/result.json').read_text(encoding='utf-8'))
        assert prior['status']=='BLOCKED' and "No module named 'soundfile'" in prior['stderr']
        label+='-adapter-fix'
    folder=OUT/label;assert not folder.exists(),'Completed/reserved step exists; inspect, do not duplicate requests'
    assert ram()>=8192,'R1 resource guard: no request reserved'
    if not native:
        ledger=OUT/'budget.json'
        if not ledger.exists():write(ledger,{'started_utc':datetime.now(UTC).isoformat(),'max_requests':3,'max_seconds':900})
        started=datetime.fromisoformat(json.loads(ledger.read_text())['started_utc'])
        deadline=started.timestamp()+900
        assert time.time()<deadline and len([p for p in OUT.iterdir() if p.is_dir() and p.name in ('asr','embedding','reranker')])<3
    else:deadline=time.time()+(50 if adapt_native else 60)  # Prior wrong ASR-only import used<10s; no package added.
    folder.mkdir();write(folder/'reservation.json',{'kind':kind,'native_only':native,'utc':datetime.now(UTC).isoformat(),'ram_free_mib':ram()})
    stdout=PRIVATE/(label+'.stdout.jsonl');stderr=PRIVATE/(label+'.stderr.txt')
    command=[str(python_for(kind)),'-I','-X','utf8',str(Path(__file__).resolve()),kind,'--child']+(['--native'] if native else [])
    samples=[];reason=None
    with stdout.open('xb') as out,stderr.open('xb') as err:
        proc=subprocess.Popen(command,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW,cwd=ROOT)
        phase='native' if native else 'load';phase_start=time.monotonic()
        while proc.poll() is None:
            time.sleep(.25);available=ram();samples.append(available)
            events=[]
            for line in stdout.read_text(encoding='utf-8').splitlines():
                try:events.append(json.loads(line))
                except ValueError:pass
            if any(e.get('phase')=='inference' for e in events) and phase!='inference':phase='inference';phase_start=time.monotonic()
            if available<4500:reason='RAM_FLOOR'
            elif time.time()>deadline:reason='TOTAL_WINDOW'
            elif time.monotonic()-phase_start>(120 if phase=='load' else 60):reason=phase.upper()+'_TIMEOUT'
            if reason:proc.terminate();proc.wait(timeout=10);break  # Only this owned child, never shared services.
    raw=stdout.read_text(encoding='utf-8');events=[]
    for line in raw.splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    status='PASSED' if proc.returncode==0 and any('result'in e for e in events) and not reason else 'BLOCKED'
    write(folder/'result.json',{'status':status,'reason':reason,'exit_code':proc.returncode,'command':command,'events':events,
        'minimum_ram_mib':min(samples) if samples else None,'stderr':stderr.read_text(encoding='utf-8'),
        'native_only':native,'model_requests_reserved':0 if native else 1})
    emit(step=label,status=status,reason=reason)
    return status

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['asr','embedding','reranker']);p.add_argument('--native',action='store_true');p.add_argument('--child',action='store_true');p.add_argument('--adapt-native',action='store_true');a=p.parse_args()
    if a.child:child(a.kind,a.native)
    else:raise SystemExit(0 if run(a.kind,a.native,a.adapt_native)=='PASSED' else 1)
