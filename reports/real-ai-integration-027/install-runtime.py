"""Approved R1 exact-wheel preparation; no inference or project environment edits."""
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/selfhost-ai-runtime'
WHEELS = PRIVATE / 'wheelhouse-cpu-20260925'
MANIFEST = ROOT / 'reports/auth-identity-ai-001/local-qwen/resources-20260925/selected-cpu-artifacts.json'
EXPECTED = '2181679185350e734276d54d7f81b6e2d889bdb864e15bd9c1cd113a9d489e01'
TARGETS = {'asr': PRIVATE/'asr-006-cpu/.venv', 'bge': PRIVATE/'bge-142-cpu/.venv'}

def save(name, value):
    path=OUT/name
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,ensure_ascii=False)

def event(**value):print(json.dumps({'received_utc':datetime.now(UTC).isoformat(),**value}),flush=True)

def main():
    raw=MANIFEST.read_bytes();assert hashlib.sha256(raw).hexdigest()==EXPECTED
    manifest=json.loads(raw);unique={}
    for env in manifest['environments']:
        assert env['name'] in TARGETS
        for a in env['artifacts']:
            assert unique.setdefault(a['expected_sha256'],a)==a
    assert len(unique)==117 and sum(a['bytes'] for a in unique.values())==384023873
    assert len({a['filename'] for a in unique.values()})==117
    assert shutil.disk_usage(ROOT).free>=8*1024**3
    assert sys.version_info[:3]==(3,12,10) and sys.maxsize>2**32
    for a in unique.values():
        u=urlsplit(a['url'])
        assert u.scheme=='https' and u.hostname in {'files.pythonhosted.org','download-r2.pytorch.org','download.pytorch.org'}
        assert not u.username and not u.password and not u.query
        assert Path(a['filename']).name==a['filename'] and a['filename'].endswith('.whl')
    spec=importlib.util.spec_from_file_location('approved_download',ROOT/'reports/auth-identity-ai-001/local-qwen/prepare-approved-resources.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    WHEELS.mkdir(parents=True,exist_ok=True)
    receipt={'started_utc':datetime.now(UTC).isoformat(),'manifest_sha256':EXPECTED,'files':[], 'environments':[], 'inference_requests':0}
    deadline=time.monotonic()+2700
    try:
        with helper.httpx.Client(timeout=helper.httpx.Timeout(60,connect=30),follow_redirects=True,trust_env=False) as client:
            for a in unique.values():
                receipt['files'].append(helper.download(client,a['url'],WHEELS/a['filename'],a['bytes'],a['expected_sha256'],'sha256',deadline))
                event(file=a['filename'],result='verified')
        uv=shutil.which('uv');assert uv
        env=os.environ.copy();env.update(UV_PYTHON_DOWNLOADS='never',UV_VENV_SEED='false')
        for entry in manifest['environments']:
            name=entry['name'];target=TARGETS[name];python=target/'Scripts/python.exe'
            if (OUT/f'install-{name}.json').exists():
                old=json.loads((OUT/f'install-{name}.json').read_text(encoding='utf-8'))
                assert old['manifest_sha256']==EXPECTED and old['status']=='INSTALLED_NOT_MODEL_VERIFIED' and python.exists()
                receipt['environments'].append(old);event(environment=name,result='previous_success_preserved');continue
            assert not target.exists(),'Unrecorded environment exists: preserve, inspect before installation'
            requirements=[]
            for a in entry['artifacts']:
                path=WHEELS/a['filename']
                assert path.stat().st_size==a['bytes'] and helper.digest(path,'sha256')==a['expected_sha256']
                requirements.append(f"{a['name']} @ {path.as_uri()} --hash=sha256:{a['expected_sha256']}")
            commands=[
                [uv,'--no-config','--offline','venv','--no-project','--no-python-downloads','--python',sys.executable,'--cache-dir',str(WHEELS/'uv-cache'),str(target)],
                [uv,'--no-config','--offline','pip','install','--python',str(python),'--no-index','--no-deps','--require-hashes','--only-binary',':all:','--no-python-downloads','--link-mode','copy','--cache-dir',str(WHEELS/'uv-cache'),'--strict','--requirements','-'],
                [uv,'--no-config','--offline','pip','check','--python',str(python)]]
            evidence={'manifest_sha256':EXPECTED,'target':str(target),'commands':[]}
            for i,command in enumerate(commands):
                result=subprocess.run(command,input='\n'.join(requirements) if i==1 else None,capture_output=True,text=True,encoding='utf-8',env=env,timeout=240)
                evidence['commands'].append({'command':command,'exit_code':result.returncode,'output':result.stdout+result.stderr})
                if result.returncode:save(f'install-{name}-failure.json',evidence);raise RuntimeError('Offline installation failed; no extra dependencies')
            code="import importlib.metadata as m,json,sys,struct;print(json.dumps({'executable':sys.executable,'version':sys.version,'prefix':sys.prefix,'bits':struct.calcsize('P')*8,'packages':{d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()}}))"
            result=subprocess.run([str(python),'-I','-B','-c',code],capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert result.returncode==0;actual=json.loads(result.stdout)
            for a in entry['artifacts']:assert actual['packages'][a['name'].lower().replace('_','-')]==a['version'],a['name']
            assert len(actual['packages'])==len(entry['artifacts']) and actual['bits']==64
            evidence.update(status='INSTALLED_NOT_MODEL_VERIFIED',actual=actual)
            save(f'install-{name}.json',evidence);receipt['environments'].append(evidence);event(environment=name,result=evidence['status'])
        receipt['status']='EXACT_ENVIRONMENTS_INSTALLED_NO_INFERENCE'
    except Exception as exc:
        receipt.update(status='STOPPED',error_type=type(exc).__name__)
        raise RuntimeError('R1 stopped; preserve receipt and completed files') from None
    finally:
        receipt['finished_utc']=datetime.now(UTC).isoformat()
        save('runtime-install-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S')+'.json',receipt)
    event(status=receipt['status'])

if __name__=='__main__':main()
