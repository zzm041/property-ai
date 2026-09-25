"""027 R2 only: guarded AUTH001 runtime, exact new knowledge tasks, no AUTO."""
import argparse
from datetime import UTC,datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import site
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'reports/real-ai-integration-027'
PRIVATE=ROOT/'tmp/real-ai-integration-027/runtime'
PINNED=PRIVATE/'candidate'
IS_RUNTIME=len(sys.argv)>1 and sys.argv[1] in ('api','worker','publisher')
APP=PINNED if IS_RUNTIME else ROOT
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path[:0]=[str(APP/'backend'),str(ROOT/'scripts/ops')]
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
from app.core.database import assert_isolated_auth_database,create_database_engine
from sqlalchemy import text

COLLECTION='knowledge_chunks_auth001_bgem3_1024'
def stamp():return datetime.now(UTC).isoformat()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def ctx():return read(PRIVATE/'context.json')
def save_ctx(v):
    # Private continuation checkpoint, never printed or staged.
    p=PRIVATE/'context.next';p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(p,PRIVATE/'context.json')
def write(name,value):
    raw=json.dumps(value,ensure_ascii=False,default=str,indent=2)
    if (PRIVATE/'context.json').exists():
        c=ctx()
        for value in [*c.get('phones',{}).values(),*c.get('tokens',{}).values(),c.get('internal_key','')]:
            if value:assert value not in raw,'Private value in public evidence'
    with (OUT/name).open('x',encoding='utf-8') as f:f.write(raw)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def settings():
    c=ctx();s=build_isolated_settings(enable_enqueue=True)
    values=s.model_dump();values.update(durable_tasks_enabled=True,
        llm_provider='production',llm_base_url='http://127.0.0.1:11436/v1',llm_model='qwen3.5:4b',llm_api_key=c['internal_key'],llm_timeout_seconds=60,llm_disable_thinking=True,llm_max_output_tokens=512,
        asr_provider='production',asr_base_url='http://127.0.0.1:11436/v1',asr_model='Qwen3-ASR-0.6B',asr_api_key=c['internal_key'],asr_timeout_seconds=120,
        embedding_provider='production',embedding_endpoint='http://127.0.0.1:11436/v1/embeddings',embedding_model='bge-m3',embedding_api_key=c['internal_key'],embedding_dimensions=1024,embedding_timeout_seconds=60,
        embedding_revision='5617a9f61b028005a4858fdac845db406aefb181',embedding_normalization='l2',
        reranker_provider='production',reranker_endpoint='http://127.0.0.1:11436/v1/rerank',reranker_model='bge-reranker-v2-m3',reranker_api_key=c['internal_key'],reranker_timeout_seconds=30,
        milvus_collection=COLLECTION,milvus_metric_type='COSINE')
    return type(s)(_env_file=None,**values)
def guard(s):
    assert_isolated_auth_database(s,database_url=s.active_database_url)
    assert s.celery_broker_url.endswith('/property_ai_auth001') and s.resolved_celery_queue=='property_ai.auth001'
    assert s.milvus_collection==COLLECTION and s.embedding_provider=='production' and s.embedding_dimensions==1024
    e=create_database_engine(s.active_database_url)
    with e.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20260925_0012_task_intents'
    e.dispose()
def configure():
    s=settings();guard(s);apply_isolated_environ(s)
    for name in ('durable_tasks_enabled','llm_provider','llm_base_url','llm_model','llm_api_key','llm_timeout_seconds','llm_disable_thinking','llm_max_output_tokens','asr_provider','asr_base_url','asr_model','asr_api_key','asr_timeout_seconds','embedding_provider','embedding_endpoint','embedding_api_key','embedding_model','embedding_revision','embedding_normalization','embedding_dimensions','embedding_timeout_seconds','reranker_provider','reranker_endpoint','reranker_api_key','reranker_model','reranker_timeout_seconds','milvus_metric_type'):
        value=getattr(s,name);os.environ[name.upper()]=str(value.get_secret_value() if hasattr(value,'get_secret_value') else value)
    return s
def source_hash(root):
    files=[(str(p.relative_to(root)).replace('\\','/'),sha(p)) for p in sorted((root/'backend/app').rglob('*.py'))]
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()
def wait(fn,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.5)
    raise RuntimeError('Bounded wait expired; inspect without replay')
def identity(pid):
    spec=importlib.util.spec_from_file_location('process026',ROOT/'reports/reliable-task-delivery-026/process026.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m.identity(pid)
def environment():
    spec=importlib.util.spec_from_file_location('env026',ROOT/'reports/reliable-task-delivery-026/environment-026.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def selected(e):
    ids=ctx()['documents']
    if not ids:return ()
    assert len(ids)<=3 and all(type(x)is int and x>0 for x in ids)
    with e.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        return tuple(c.execute(text("SELECT i.id FROM task_intents i JOIN knowledge_ingestion_jobs j ON i.operation='INGEST_KNOWLEDGE' AND i.resource_id=j.id JOIN knowledge_documents d ON d.id=j.document_id WHERE d.id IN ("+','.join(map(str,ids))+") AND d.title LIKE :marker"),{'marker':ctx()['run_id']+'%'}).scalars())

def prepare():
    PRIVATE.mkdir(parents=True,exist_ok=True)
    assert not (PRIVATE/'context.json').exists(),'Resume existing run, no fresh identities'
    previous=read(ROOT/'tmp/reliable-task-delivery-026/normal-integration/context.json')
    c={k:previous[k] for k in ('phones','identities','tenant_id','property_id','houses')}
    c.update(run_id='ai027-'+uuid4().hex[:10],documents=[],tokens={},internal_key=secrets.token_urlsafe(36),processes={},steps={},last_sms_sent_at={})
    save_ctx(c);guard(settings())
    e=environment();_,_,idle=e.idle(settings())
    queues=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True)
    write('preflight.json',{'utc':stamp(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'run_id':c['run_id'],'database':'127.0.0.1:3307/property_ai_auth001_test@20260925_0012_task_intents',
        'vhost':'property_ai_auth001','queue':'property_ai.auth001','worker_counts':idle,'queue_metadata':queues,
        'protected_config_sha256':sha(ROOT/'miniprogram/project.config.json'),'no_business_writes':True,'model_requests':0})
    print(json.dumps({'prepared':c['run_id'],'worker_idle':idle}))

def pin():
    assert not PINNED.exists(),'Never overwrite a running candidate'
    receipt=read(ROOT/'reports/auth-identity-ai-001/local-qwen/resources-20260925/models-result-connection-retry.json')
    assert len(receipt['files'])==26
    checked=[]
    for item in receipt['files']:
        path=Path(item['path']);assert path.is_relative_to(ROOT/'tmp/selfhost-ai-local/models-hf')
        digest=hashlib.sha256()
        with path.open('rb') as f:
            while block:=f.read(8*1024*1024):digest.update(block)
        assert path.stat().st_size==item['bytes'] and digest.hexdigest()==item['sha256']
        checked.append({'path':str(path),'bytes':item['bytes'],'sha256':digest.hexdigest()})
    write('verified-models.json',{'files':checked,'network_requests':0,'model_calls':0,'utc':stamp()})
    files=list((ROOT/'backend/app').rglob('*.py'))+[ROOT/'scripts/ops/local_ai_http.py',ROOT/'scripts/ops/local_ai_models.py',ROOT/'scripts/ops/local_ai_process.py']
    entries=[]
    for p in files:
        dest=PINNED/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        entries.append({'path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':sha(p)})
    write('candidate.json',{'base_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_root':str(PINNED),'source_sha256':source_hash(PINNED),'files':entries,'scope':'R2_ONLY; R3 diagnostic NOT_READY, no work-order inference authorized'})
    config={'host':'127.0.0.1','port':11436,'private':str(PRIVATE/'model-calls'),'model_root':str(ROOT/'tmp/selfhost-ai-local/models-hf'),
        'asr_python':str(ROOT/'tmp/selfhost-ai-runtime/asr-006-cpu/.venv/Scripts/python.exe'),'bge_python':str(ROOT/'tmp/selfhost-ai-runtime/bge-142-cpu/.venv/Scripts/python.exe')}
    (PRIVATE/'model-config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    print(json.dumps({'pinned':source_hash(PINNED),'model_calls':0}))

def model_start():
    c=ctx();assert 'models' not in c['processes']
    config=read(PRIVATE/'model-config.json')
    import socket
    with socket.socket() as sock:sock.bind(('127.0.0.1',11436))
    env=dict(os.environ);env['LOCAL_AI_INTERNAL_TOKEN']=c['internal_key']
    cmd=[config['bge_python'],'-I','-X','utf8',str(PINNED/'scripts/ops/local_ai_http.py'),'serve','--config',str(PRIVATE/'model-config.json')]
    with (PRIVATE/'models.stdout.log').open('xb') as out,(PRIVATE/'models.stderr.log').open('xb') as err:
        p=subprocess.Popen(cmd,stdout=out,stderr=err,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    c['processes']['models']=wait(lambda:identity(p.pid),5);save_ctx(c)
    import httpx
    def health():
        try:return httpx.get('http://127.0.0.1:11436/healthz',headers={'Authorization':'Bearer '+c['internal_key']},trust_env=False,timeout=1).json()
        except (httpx.HTTPError,ValueError):return None
    result=wait(health,10);assert result['inference']is False
    write('model-service.json',{'process':c['processes']['models'],'health':result,'config':config,'calls':0})
    print('Model HTTP ready; zero inference')

def model_fix():
    c=ctx();old=c['processes']['models'];actual=identity(old['ProcessId'])
    assert actual and actual['created']==old['created'] and actual['CommandLine']==old['CommandLine']
    assert 'candidate\\scripts\\ops\\local_ai_http.py' in actual['CommandLine'] or 'candidate/scripts/ops/local_ai_http.py' in actual['CommandLine']
    for p in (PRIVATE/'model-calls').glob('call-*/reservation.json'):assert (p.parent/'result.json').exists(),'A model request is still active/unknown'
    folder=PRIVATE/'model-adapter-fix1';folder.mkdir(exist_ok=True)
    for name in ('local_ai_http.py','local_ai_models.py','local_ai_process.py'):
        if (folder/name).exists():assert sha(folder/name)==sha(ROOT/'scripts/ops'/name)
        else:shutil.copy2(ROOT/'scripts/ops'/name,folder/name)
    # Only this batch's idle local HTTP process; no model/other process names.
    command=f"""$ErrorActionPreference='Stop'
$p=Get-CimInstance Win32_Process -Filter 'ProcessId={old['ProcessId']}'
if($p.CreationDate.ToString('o') -ne '{old['created']}'){{throw 'Identity changed'}}
$children=@(Get-CimInstance Win32_Process -Filter 'ParentProcessId={old['ProcessId']}')
$server=@($children | Where-Object {{$_.ExecutablePath -eq 'D:\\property-ai\\tmp\\uv-python\\cpython-3.12.10-windows-x86_64-none\\python.exe'}})
if($server.Count -ne 1){{throw 'Unexpected server child'}}
$expected='-I -X utf8 D:\\property-ai\\tmp\\real-ai-integration-027\\runtime\\candidate\\scripts\\ops\\local_ai_http.py serve --config D:\\property-ai\\tmp\\real-ai-integration-027\\runtime\\model-config.json'
if(-not $server[0].CommandLine.EndsWith($expected)){{throw 'Unexpected child command'}}
if(@($children | Where-Object {{$_.ProcessId -ne $server[0].ProcessId -and $_.ExecutablePath -ne 'C:\\WINDOWS\\system32\\conhost.exe'}}).Count){{throw 'Unknown child'}}
if(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$server[0].ProcessId)){{throw 'Inference child still running'}}
$listener=@(Get-NetTCPConnection -LocalPort 11436 -State Listen)
if($listener.Count -ne 1 -or $listener[0].LocalAddress -ne '127.0.0.1' -or $listener[0].OwningProcess -ne $server[0].ProcessId){{throw 'Listener ownership changed'}}
Stop-Process -Id $server[0].ProcessId -ErrorAction Stop
"""
    subprocess.run(['powershell','-NoProfile','-Command',command],check=True);wait(lambda:identity(old['ProcessId'])is None,5)
    config=read(PRIVATE/'model-config.json');env=dict(os.environ);env['LOCAL_AI_INTERNAL_TOKEN']=c['internal_key']
    with (PRIVATE/'models-fix1.stdout.log').open('xb') as out,(PRIVATE/'models-fix1.stderr.log').open('xb') as err:
        p=subprocess.Popen([config['bge_python'],'-I','-X','utf8',str(folder/'local_ai_http.py'),'serve','--config',str(PRIVATE/'model-config.json')],stdout=out,stderr=err,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    c['processes']['models']=wait(lambda:identity(p.pid),5);save_ctx(c)
    write('model-adapter-fix1.json',{'old_process':old,'new_process':c['processes']['models'],'reason':'Wait for own idle Qwen release while retaining8192MiB CPU start guard; no probe/retry','source_sha256':sha(folder/'local_ai_http.py'),'budget_directory':str(PRIVATE/'model-calls'),'budget_reset':False,'business_source_unchanged':source_hash(PINNED)})
    print('Model adapter fixed; original R2 deadline/calls retained')

def collection():
    s=settings();guard(s)
    from app.provider.vector import MilvusKnowledgeStore
    from pymilvus import utility,Collection
    store=MilvusKnowledgeStore(s);store.connect()
    exists=utility.has_collection(COLLECTION,using=store._alias)
    # Existing exact metadata is checked before load, never delete or alter it.
    result=store.ensure_collection(1024)
    old=Collection('knowledge_chunks_auth001',using=store._alias)
    write('collection.json',{'utc':stamp(),'name':COLLECTION,'existed_before':exists,'schema':result.schema.to_dict(),
        'indexes':[i.params for i in result.indexes],'legacy_schema':old.schema.to_dict(),'legacy_modified':False,'model_calls':0})
    print('New isolated collection ready; legacy8 unchanged')

def replace():
    c=ctx();assert not any(k in c['processes'] for k in ('api','worker','publisher'))
    manifest=read(OUT/'candidate.json');assert source_hash(PINNED)==manifest['source_sha256']
    e=environment();app,target,counts=e.idle(settings())
    old=read(ROOT/'tmp/reliable-task-delivery-026/normal-integration/context.json')['processes']
    observed={name:identity(old[name]['ProcessId']) for name in ('api','worker')}
    for name,p in observed.items():
        assert p and p['created']==old[name]['created'] and p['CommandLine']==old[name]['CommandLine']
        assert 'normal-integration.py' in p['CommandLine']
    queues=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True)
    row=next(line.split() for line in queues.splitlines() if line.startswith('property_ai.auth001\t'))
    assert row==['property_ai.auth001','0','0','1'],row
    app.control.broadcast('shutdown',destination=[target]);wait(lambda:identity(observed['worker']['ProcessId'])is None,30)
    # Old API has its own graceful stop file. No PID kill or shared service stop.
    (ROOT/'tmp/reliable-task-delivery-026/normal-integration/stop-api').touch(exist_ok=False)
    wait(lambda:identity(observed['api']['ProcessId'])is None,30)
    write('replacement.json',{'old':observed,'idle':counts,'queues':queues,'method':'targeted graceful Worker shutdown; own API stop flag','scope':'R2 only','utc':stamp()})
    for part in ('api','worker','publisher'):
        with (PRIVATE/(part+'.stdout.log')).open('xb') as out,(PRIVATE/(part+'.stderr.log')).open('xb') as err:
            p=subprocess.Popen([sys.executable,'-I','-X','utf8',str(Path(__file__).resolve()),part],stdout=out,stderr=err,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        c=ctx();c['processes'][part]=wait(lambda:identity(p.pid),5);save_ctx(c)
    import httpx
    def health():
        try:return httpx.get('http://127.0.0.1:8083/api/v1/healthz',trust_env=False,timeout=1).status_code==200
        except httpx.HTTPError:return False
    wait(health,30);print('Same-source R2 API/Worker/publisher ready; no new work orders')

def serve(part):
    manifest=read(OUT/'candidate.json');assert source_hash(PINNED)==manifest['source_sha256']
    s=configure();print(json.dumps({'part':part,'pid':os.getpid(),'utc':stamp(),'source_root':str(PINNED),'source_sha256':source_hash(PINNED),'collection':COLLECTION,'providers':'real R2 only; AUTO NOT_READY','database':'AUTH001@0012','vhost':'property_ai_auth001','queue':'property_ai.auth001'}),flush=True)
    if part=='api':
        import uvicorn
        from app.main import create_app
        server=uvicorn.Server(uvicorn.Config(create_app(s),host='127.0.0.1',port=8083,log_level='warning',access_log=False))
        def stop():
            while not (PRIVATE/'stop-api').exists():time.sleep(1)
            server.should_exit=True
        threading.Thread(target=stop,daemon=True).start();server.run()
    elif part=='publisher':
        from app.worker.task_intents import run_publisher
        e=create_database_engine(s.active_database_url)
        try:run_publisher(e,s,ids_supplier=lambda:selected(e),seconds=3600)
        finally:e.dispose()
    else:
        from app.worker.task_intents import celery_app
        celery_app.worker_main(['worker','-Q','property_ai.auth001','--pool=solo','--loglevel=warning','--hostname=auth001@%h','--without-mingle','--without-gossip'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','pin','model-start','model-fix','collection','replace','api','worker','publisher']);a=p.parse_args()
    if a.mode in ('api','worker','publisher'):serve(a.mode)
    else:{'prepare':prepare,'pin':pin,'model-start':model_start,'model-fix':model_fix,'collection':collection,'replace':replace}[a.mode]()
