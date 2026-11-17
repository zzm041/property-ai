"""Approved normal-default 026 integration, two new orders; no crash replay.

Only the new run's exact task intents/notifications are published. The sole
Development failure is named in the private run contract, generation1 only.
No migration, SQL business mutation, old task replay or real inference.
"""
import argparse
from dataclasses import asdict
import importlib.util
import os
from pathlib import Path
import runpy
import site
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
from datetime import UTC, datetime
import hashlib
import json

ROOT=Path(__file__).resolve().parents[2]
PINNED=ROOT/'tmp/reliable-task-delivery-026/normal-integration/source-ed66d37'
IS_RUNTIME=len(sys.argv)>1 and sys.argv[1] in ('api','worker','publisher','supervisor','pin-check')
APP_ROOT=PINNED if IS_RUNTIME else ROOT
if IS_RUNTIME:assert (PINNED/'backend/app/main.py').is_file(),'Pinned reviewed source required; never load workspace WIP'
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path[:0]=[str(APP_ROOT/'backend'),str(ROOT/'scripts/ops'),str(Path(__file__).parent)]
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
from app.core.database import create_database_engine,assert_isolated_auth_database
from sqlalchemy import text
def settings():return build_isolated_settings(enable_enqueue=True).model_copy(update={'durable_tasks_enabled':True})
def guard(s):
    assert_isolated_auth_database(s,database_url=s.active_database_url)
    assert s.celery_broker_url.endswith('/property_ai_auth001') and s.resolved_celery_queue=='property_ai.auth001'
    engine=create_database_engine(s.active_database_url)
    with engine.connect() as c:assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20260925_0012_task_intents'
    engine.dispose()
def stamp():return datetime.now(UTC).isoformat()
def source_hash():
    entries=[(str(p.relative_to(APP_ROOT)).replace('\\','/'),hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()) for p in sorted((APP_ROOT/'backend/app').rglob('*.py'))]
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()
base=SimpleNamespace(apply_isolated_environ=apply_isolated_environ,
    context=lambda:json.loads((ROOT/'tmp/reliable-task-delivery-026/runtime/context.json').read_text(encoding='utf-8')),
    client=lambda run:runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))['Client'](run))
OUT = Path(__file__).parent / 'normal-integration'
PRIVATE = ROOT / 'tmp/reliable-task-delivery-026/normal-integration'

def ctx(): return json.loads((PRIVATE/'context.json').read_text(encoding='utf-8'))
def save_ctx(value):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE/'context.json').write_text(json.dumps(value, indent=2), encoding='utf-8')
def write(name, value, *, exclusive=True):
    OUT.mkdir(exist_ok=True)
    raw=json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if (PRIVATE/'context.json').exists():
        current=ctx()
        for phone in current.get('phones',{}).values(): assert phone.removeprefix('+86') not in raw
        for token in current.get('tokens',{}).values(): assert token not in raw
    with (OUT/name).open('x' if exclusive else 'w', encoding='utf-8') as f: f.write(raw)
def rows(engine, sql, params=None):
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        return [dict(r) for r in c.execute(text(sql), params or {}).mappings()]
def selected(engine):
    current=ctx(); params={'prefix': current['run_id']+'-%'}
    result=rows(engine,'SELECT id,operation,resource_id,generation,status,execution_attempt,publish_attempt,task_id,resource_version,last_error_code,finished_at FROM task_intents WHERE request_id LIKE :prefix ORDER BY id',params)
    order_ids=list(current['orders'].values())
    if order_ids:
        assert len(order_ids)<=2 and all(type(i) is int and i>0 for i in order_ids)
        placeholders=','.join(str(i) for i in order_ids)
        result+=rows(engine,"SELECT i.id,i.operation,i.resource_id,i.generation,i.status,i.execution_attempt,i.publish_attempt,i.task_id,i.resource_version,i.last_error_code,i.finished_at FROM task_intents i JOIN notification_deliveries d ON i.operation='DELIVER_NOTIFICATION' AND i.resource_id=d.id JOIN notifications n ON n.id=d.notification_id WHERE n.resource_type='WORK_ORDER' AND n.resource_id IN ("+placeholders+") ORDER BY i.id")
    return sorted({r['id']:r for r in result}.values(),key=lambda r:r['id'])
def environment():
    spec=importlib.util.spec_from_file_location('env026',Path(__file__).parent/'environment-026.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
def identity(pid):
    from process026 import identity as identify
    return identify(pid)
def wait(fn, seconds=120):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.5)
    raise RuntimeError('Normal integration bounded wait expired; retain object, no new run')
def configured():
    s=settings();base.apply_isolated_environ(s)
    os.environ['DURABLE_TASKS_ENABLED']='true'
    for name in ('LLM','VLM','ASR','EMBEDDING','RERANKER','NOTIFICATION'):os.environ[name+'_PROVIDER']='development'
    os.environ['LLM_PROVIDER_BEHAVIOR']='success'
    guard(s)
    return s
def serve(component):
    # Fail before any service connection if the retained runtime snapshot drifted.
    assert source_hash()=='fe9fcdd742ffe6331fb440f94019a067a9325aaa9a9b91c2d8a009ea615c8926','Reviewed runtime snapshot changed'
    s=configured(); current=ctx()
    from app.service.task_intent import IntentTiming
    assert asdict(IntentTiming())==dict(publish_lease=15,claim_deadline=120,execution_lease=120,heartbeat=20,execution_deadline=900)
    print(json.dumps({'component':component,'pid':os.getpid(),'run_id':current['run_id'],'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'runtime_product_commit':'ed66d37d2315ca26a8b60aaa48443193201f7b84','app_source_root':str(APP_ROOT/'backend/app'),'source_sha256':source_hash(),'python':sys.executable,'default_timing':asdict(IntentTiming()),'providers':'Development','durable':True,'database':'127.0.0.1:3307/property_ai_auth001_test@0012','vhost':'property_ai_auth001','queue':'property_ai.auth001','utc':stamp()}),flush=True)
    if component=='api':
        import uvicorn
        from app.main import create_app
        server=uvicorn.Server(uvicorn.Config(create_app(s),host='127.0.0.1',port=8083,log_level='warning',access_log=False))
        def stopper():
            while not (PRIVATE/'stop-api').exists(): time.sleep(1)
            server.should_exit=True
        threading.Thread(target=stopper,daemon=True).start();server.run()
    elif component=='publisher':
        from app.worker.task_intents import run_publisher
        engine=create_database_engine(s.active_database_url)
        try:run_publisher(engine,s,ids_supplier=lambda:tuple(r['id'] for r in selected(engine)),seconds=900)
        finally:engine.dispose()
    elif component=='supervisor':
        from supervise_task_publisher import supervise
        command=[sys.executable,'-I','-X','utf8',str(Path(__file__).resolve()),'publisher']
        supervise(command,cycles=24,cwd=ROOT,stop_file=PRIVATE/'stop-publisher')
    else:
        from app.worker import task_intents
        engine=create_database_engine(s.active_database_url)
        domain=task_intents._run_domain
        def controlled(session,settings,lease):
            # Approved precise synthetic generation only. No short lease/clock,
            # checkpoint or process-termination hook. The formal retry is real.
            if (lease['request_id']==ctx()['run_id']+'-create-recovery'
                    and lease['operation']=='ANALYZE_WORK_ORDER'):
                from app.provider.errors import ProviderUnavailableError
                raise ProviderUnavailableError('Named normal-integration Development failure')
            return domain(session,settings,lease)
        task_intents._run_domain=controlled
        from app.repository.ai import AiRepository
        from app.service.sla import SlaService
        waiting=AiRepository.list_waiting_dispatch
        def scoped_waiting(self,**kw):
            ids=tuple(ctx()['orders'].values())
            return waiting(self,**{**kw,'work_order_ids':ids,'limit':2}) if ids else ()
        AiRepository.list_waiting_dispatch=scoped_waiting
        SlaService.sweep_unavailable_assignments=lambda self,**kw:0
        task_intents.celery_app.worker_main(['worker','-Q','property_ai.auth001','--pool=solo','--loglevel=warning','--hostname=auth001@%h','--without-mingle','--without-gossip'])

def preflight():
    manifest=json.loads((Path(__file__).parent/'review-fix1-manifest.json').read_text(encoding='utf-8'))
    mismatch=[p for p,h in manifest['files'].items() if hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=h]
    assert not mismatch, mismatch
    e=environment();s=settings();report=e.receipt(s)
    assert report['revision']=='20260925_0012_task_intents'
    _,_,report['worker_counts']=e.idle(s)
    report.update(final_source_files=25,hash_mismatches=[],source_sha256=source_hash(),quota_owner_baseline=40,reserved_points=15,deadline='2026-09-27T14:00:00+08:00')
    write('preflight.json',report)
    if not (PRIVATE/'context.json').exists():
        old=base.context()
        current={k:old[k] for k in ('phones','identities','tenant_id','property_id','employee_id','houses')}
        current.update(run_id='rt026n-'+uuid4().hex[:10],orders={},tokens={},steps={},processes={},started_utc=stamp())
        save_ctx(current)
    print(json.dumps({'preflight':'passed','run_id':ctx()['run_id'],'source_files':25,'source_sha256':source_hash()}))
def replace():
    current=ctx(); assert not current['processes'], 'Already started; inspect instead'
    e=environment();s=settings();before=e.receipt(s)
    app,target,counts=e.idle(s)
    old=before['processes'];assert len(old)==2 and before['active_other_db_connections']==0
    assert all('launch-026.py' in p['CommandLine'] and 'rt026-91880f6aeb' in p['CommandLine'] for p in old)
    worker=next(p for p in old if ' worker ' in p['CommandLine']);api=next(p for p in old if ' api ' in p['CommandLine'])
    app.control.broadcast('shutdown',destination=[target])
    wait(lambda:identity(worker['ProcessId']) is None,30)
    actual=identity(api['ProcessId']); assert actual and actual['CommandLine']==api['CommandLine'] and actual['created']==api['Created']
    assert e.receipt(s)['active_other_db_connections']==0
    subprocess.run(['powershell','-NoProfile','-Command',f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={api['ProcessId']}';if($p.CreationDate.ToString('o') -ne '{api['Created']}'){{throw 'Identity changed'}};Stop-Process -Id {api['ProcessId']} -ErrorAction Stop"],check=True)
    wait(lambda:identity(api['ProcessId']) is None,5)
    write('replacement.json',{'before':before,'idle':counts,'method':'targeted graceful Celery shutdown; old idle API Windows termination (no shutdown hook), not a crash test','utc':stamp()})
    for component in ('api','worker','supervisor'):
        with (PRIVATE/(component+'.stdout.log')).open('xb') as out,(PRIVATE/(component+'.stderr.log')).open('xb') as err:
            p=subprocess.Popen([sys.executable,'-I','-X','utf8',str(Path(__file__).resolve()),component],cwd=ROOT,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(.3);record=identity(p.pid);assert record
        current=ctx();current['processes'][component]=record;save_ctx(current)
    import httpx
    def ready():
        try:return httpx.get('http://127.0.0.1:8083/api/v1/healthz',trust_env=False,timeout=1).status_code==200
        except httpx.HTTPError:return False
    wait(ready,30)
    print(json.dumps({'started':{k:v['ProcessId'] for k,v in ctx()['processes'].items()},'real_model_calls':0}))

def verify():
    current=ctx();assert 'finished_utc' not in current,'Completed integration must not rerun'
    s=settings();guard(s);engine=create_database_engine(s.active_database_url)
    c=base.client(current['run_id'])
    import httpx
    c.http.close();c.http=httpx.Client(base_url='http://127.0.0.1:8083/api/v1',timeout=30,trust_env=False)
    evidence_path=OUT/'business.json'
    evidence=json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path.exists() else {'run_id':current['run_id'],'steps':{},'requests':[]}
    def request(method,path,actor='resident',**kw):
        return c.data(method,path,token=ctx()['tokens'][actor],**kw)
    def save():
        evidence['requests'].extend(c.requests);c.requests.clear();write('business.json',evidence,exclusive=False)
    def step(name,fn):
        if name in evidence['steps']:return
        evidence['steps'][name]=fn();save();print(json.dumps({'step':name,'result':'saved'}),flush=True)
    def auth():
        for actor in ('resident','staff','manager'):
            current=ctx()
            if actor not in current['tokens']:
                current['tokens'][actor]=c.sms_login(current['phones'][actor],actor)['access_token'];save_ctx(current)
            me=request('GET','/me',actor=actor,label='identity-'+actor)
            assert me['user_id']==current['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
        daily=request('GET','/staff/attendance/today',actor='staff',params={'tenant_id':ctx()['tenant_id'],'property_id':ctx()['property_id']},label='attendance')
        assert daily['daily_status']['on_duty']=='ON_DUTY','Existing staff not currently on duty; no SQL change'
        return {'formal_auth':True,'daily':daily,'identity_ids':ctx()['identities']}
    def create(label):
        current=ctx()
        if label in current['orders']:return current['orders'][label]
        assert len(current['orders'])<2
        # Idempotency key retained across a response loss; no extra object.
        result=request('POST','/work-orders',body={'house_id':current['houses']['prepare' if label=='normal' else 'dispatch'],'contact_phone':current['phones']['resident'],'text':current['run_id']+'：水龙头滴水，无烟无火花，请维修组处理。'},headers={'Idempotency-Key':current['run_id']+'-'+label},label='create-'+label)
        found=rows(engine,'SELECT DISTINCT work_order_id AS id FROM work_order_logs WHERE request_id=:r',{'r':current['run_id']+'-create-'+label})
        assert len(found)==1
        current['orders'][label]=found[0]['id'];save_ctx(current);save()
        return found[0]['id']
    def detail(oid):return request('GET',f'/work-orders/{oid}',label='detail')['work_order']
    def record(oid):
        return {'work':rows(engine,'SELECT id,status,version,current_assignment_id FROM work_orders WHERE id=:id',{'id':oid}),
                'assignments':rows(engine,'SELECT id,employee_id,assigned_source,status FROM work_order_assignments WHERE work_order_id=:id ORDER BY id',{'id':oid}),
                'analysis':rows(engine,'SELECT id,status,analysis_version,error_code FROM work_order_analyses WHERE work_order_id=:id ORDER BY id',{'id':oid}),
                'calls':rows(engine,'SELECT id,provider_type,attempt,status,model_version FROM ai_call_logs WHERE work_order_id=:id ORDER BY id',{'id':oid}),
                'intents':[r for r in selected(engine) if r['operation']=='ANALYZE_WORK_ORDER' and r['resource_id']==oid]}
    def normal():
        oid=create('normal');wait(lambda:detail(oid)['status']=='ASSIGNED')
        result=record(oid);assert len(result['assignments'])==1 and result['assignments'][0]['assigned_source']=='AUTO'
        assert result['intents'][0]['status']=='SUCCEEDED'
        return result
    def release():
        oid=ctx()['orders']['normal'];d=detail(oid)
        for old,action in [('ASSIGNED','accept'),('ACCEPTED','start')]:
            if d['status']==old:request('POST',f'/staff/work-orders/{oid}/{action}',actor='staff',body={'version':d['version']},label=action);d=detail(oid)
        if d['status']=='PROCESSING':request('POST',f'/staff/work-orders/{oid}/receipts',actor='staff',body={'version':d['version'],'problem_type':'普通维修','method':'本轮合成滴水问题已处理并核对。','resolved':True},label='receipt')
        d=detail(oid)
        if d['status']=='WAIT_CONFIRM':request('POST',f'/work-orders/{oid}/confirm',body={'version':d['version']},label='confirm')
        assert detail(oid)['status']=='CLOSED'
        return {'work_order_id':oid,'status':'CLOSED','purpose':'release the single real idle slot for recovery case; not025rerun'}
    def recovery():
        oid=create('recovery')
        def failed():return next((r for r in selected(engine) if r['operation']=='ANALYZE_WORK_ORDER' and r['resource_id']==oid and r['generation']==1 and r['status']=='FAILED'),None)
        old=wait(failed);assert old['execution_attempt']==3
        # Compare complete persistent row privately; publish hash only.
        before=rows(engine,'SELECT * FROM task_intents WHERE id=:id',{'id':old['id']})
        denied=c.call('POST',f"/admin/task-intents/{old['id']}/retry",token=ctx()['tokens']['resident'],body={'reason_code':'DEPENDENCY_RECOVERED'},label='retry-denied',expected=(404,))[0]
        managed=request('GET',f"/admin/task-intents/{old['id']}",actor='manager',label='inspect-failed')
        new=request('POST',f"/admin/task-intents/{old['id']}/retry",actor='manager',body={'reason_code':'DEPENDENCY_RECOVERED'},label='retry-authorized')
        again=request('POST',f"/admin/task-intents/{old['id']}/retry",actor='manager',body={'reason_code':'DEPENDENCY_RECOVERED'},label='retry-duplicate')
        assert new['id']==again['id'] and new['generation']==2
        wait(lambda:detail(oid)['status']=='ASSIGNED')
        result=record(oid);assert len(result['assignments'])==1 and result['assignments'][0]['assigned_source']=='AUTO'
        assert rows(engine,'SELECT * FROM task_intents WHERE id=:id',{'id':old['id']})==before
        audits=rows(engine,"SELECT id,actor_user_id,action,resource_id,details_json FROM audit_logs WHERE action='TASK_INTENT_RETRIED' AND resource_id=:id",{'id':new['id']})
        assert len(audits)==1
        result.update(old_unchanged_sha256=hashlib.sha256(json.dumps(before,sort_keys=True,default=str).encode()).hexdigest(),denied_status=denied,managed=managed,new=new,duplicate=again,audits=audits)
        return result
    try:
        step('auth',auth);step('normal_AUTO',normal);step('release_normal_capacity',release);step('formal_recovery',recovery)
        wait(lambda:all(r['status'] in ('SUCCEEDED','FAILED','CANCELLED') for r in selected(engine)))
        evidence['final_intents']=selected(engine)
        evidence['employee_load']=rows(engine,'SELECT business_date,current_load,on_duty FROM employee_daily_statuses WHERE employee_id=:id ORDER BY business_date',{'id':ctx()['employee_id']})
        current=ctx();current['finished_utc']=stamp();save_ctx(current)
    except Exception as exc:
        evidence.setdefault('errors',[]).append({'utc':stamp(),'type':type(exc).__name__,'message':str(exc) if type(exc) in (AssertionError,RuntimeError) else 'private diagnostic only'})
        raise
    finally:save();c.close();engine.dispose()

def closeout():
    current=ctx();assert 'finished_utc' in current
    before=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
    after=environment().receipt(settings())
    assert after['protected']==before['protected']
    startups=[]
    for component,record in current['processes'].items():
        assert identity(record['ProcessId'])==record
        for line in (PRIVATE/(component+'.stdout.log')).read_text(encoding='utf-8').splitlines():
            try:item=json.loads(line)
            except ValueError:continue
            if item.get('component'):startups.append(item)
    assert {'api','worker','publisher','supervisor'}<={x['component'] for x in startups}
    assert all(x['source_sha256']==before['source_sha256'] for x in startups)
    after.update(actual_processes=current['processes'],startup_records=startups,run_id=current['run_id'],orders=current['orders'],
                 runtime_source_sha256=before['source_sha256'],current_source_sha256=source_hash(),
                 control_boundary='No timing/kill checkpoints. Exact new recovery request generation1 alone used Development unavailable; it is immutable FAILED. Successor is normal product execution. No global failure switch.',
                 supervisor_boundary='Local finite24x900s cycles, successful child exit auto-restart; three consecutive failures stop. No global service; production24x7 not accepted.',
                 new_knowledge_versions=0,new_model_calls=0,old_protected_fingerprints_unchanged=True)
    write('closeout.json',after)
    print(json.dumps({'run_id':current['run_id'],'orders':current['orders'],'source_sha256':before['source_sha256'],'protected_unchanged':True}))

def continuity():
    """Read only: preserve natural cycles and exact terminal intent equality."""
    current=ctx();assert 'finished_utc' in current
    events=[]
    for line in (PRIVATE/'supervisor.stdout.log').read_text(encoding='utf-8').splitlines():
        try:item=json.loads(line)
        except ValueError:continue
        if item.get('component') or item.get('source')=='local-publisher-supervisor':events.append(item)
    publishers=[e for e in events if e.get('component')=='publisher']
    assert len(publishers)>=3 and publishers[-1]['source_sha256']=='fe9fcdd742ffe6331fb440f94019a067a9325aaa9a9b91c2d8a009ea615c8926'
    assert publishers[-1]['app_source_root']==str(PINNED/'backend/app')
    alive=identity(publishers[-1]['pid']);assert alive and alive['CommandLine'].endswith(' publisher')
    for record in current['processes'].values():assert identity(record['ProcessId'])==record
    s=settings();guard(s);engine=create_database_engine(s.active_database_url)
    try:actual=selected(engine)
    finally:engine.dispose()
    saved=json.loads((OUT/'business.json').read_text(encoding='utf-8'))['final_intents']
    assert json.loads(json.dumps(actual,default=str))==saved, 'Intent state changed since completed business evidence'
    queues=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True).strip()
    write('supervision-natural-cycles.json',{
        'observed_utc':stamp(),'run_id':current['run_id'],'events':events,
        'current_publisher':alive,'unchanged_api_worker_supervisor':current['processes'],
        'completed_intents_unchanged':True,'queue_metadata':queues,
        'retained_drift':'Second publisher loaded workspace AI WIP at22:05:45Z; no new business was submitted, selected terminal task records are unchanged. This interval is not claimed same-source runtime.',
        'correction':'Next natural child uses reviewed ed66d37 snapshot and asserts canonical hash before service connection; no forced restart, process termination, business or inference.',
        'limits':'Two natural finite exits only, not production24x7 or crash/endurance proof. Scope remains exact completed run; historical tasks not scanned.'})
    print(json.dumps({'continuity':'verified','publisher':publishers[-1]['pid'],'business_calls':0,'model_calls':0}))

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','replace','verify','closeout','continuity','pin-check','api','worker','publisher','supervisor']);a=p.parse_args()
    if a.action=='pin-check':
        import app.core.config as config
        actual=source_hash();assert actual=='fe9fcdd742ffe6331fb440f94019a067a9325aaa9a9b91c2d8a009ea615c8926'
        assert Path(config.__file__).is_relative_to(PINNED)
        write('pin-check.json',{'source_sha256':actual,'module':config.__file__,'archive_sha256':hashlib.sha256((PRIVATE/'source-ed66d37.zip').read_bytes()).hexdigest(),'business_calls':0,'model_calls':0,'reason':'Future naturally restarted publishers use reviewed product snapshot, not concurrent workspace AI changes'})
        print(json.dumps({'source_sha256':actual,'module':config.__file__,'business_calls':0}))
    elif a.action in ('api','worker','publisher','supervisor'):serve(a.action)
    else:globals()[a.action]()
if __name__=='__main__':main()
