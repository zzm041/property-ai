"""One persisted15min run, <=3orders/1version,3API-publisher+2Worker kills.

Requests are normal authenticated business APIs. SQL is SELECT-only. Test
controls live in the dedicated launcher; never replay old objects or DLQ.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sys
from pathlib import Path
import time
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
from process026 import start,kill,identity
from sqlalchemy.orm import Session
from app.service.task_intent import TaskIntentService

ctx=context();s=settings();guard(s);c=client(ctx['run_id']);engine=create_database_engine(s.active_database_url)
e_path=OUT/'runtime-evidence.json'
e=json.loads(e_path.read_text(encoding='utf-8')) if e_path.exists() else {'task_id':'RELIABLE-TASK-DELIVERY-026','run_id':ctx['run_id'],'mode':'real normal API/DB/independent Worker + explicitly marked test controls; no UI or model calls','steps':{},'requests':[]}
def save():
    e['requests'].extend(c.requests);c.requests.clear();e['updated_utc']=stamp()
    write('runtime-evidence.json',e,exclusive=False)
def budget():
    current=context();assert 'drill_started' in current
    assert 'drill_finished' not in current, 'Completed drill is immutable; no reset or replay'
    assert (datetime.now(UTC)-datetime.fromisoformat(current['drill_started'])).total_seconds()<900,'Whole run15min limit'
def wait(fn,seconds=105):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        budget()
        value=fn()
        if value:return value
        time.sleep(.2)
    raise RuntimeError('Window wait exceeded finite bound')
def req(method,path,actor='resident',**kw):
    return c.data(method,path,token=context()['tokens'][actor],label=kw.pop('label','observe'),**kw)
def control(window,component,stage,intent_id=None):
    budget();(PRIVATE/'control.json').write_text(json.dumps({'window':window,'component':component,'stage':stage,'intent_id':intent_id}),encoding='utf-8')
def clear(): (PRIVATE/'control.json').write_text('{}',encoding='utf-8')
def checkpoint(window):
    path=PRIVATE/(window+'-checkpoint.json')
    return wait(lambda:json.loads(path.read_text(encoding='utf-8')) if path.exists() else None)
def api_ready():
    import httpx
    try:return httpx.get('http://127.0.0.1:8083/api/v1/healthz',timeout=1,trust_env=False).status_code==200
    except httpx.HTTPError:return False
def rows(sql,params=None):
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        return [dict(r) for r in connection.execute(text(sql),params or {}).mappings()]
def intents():
    selected=rows('SELECT id,operation,resource_id,resource_version,generation,task_id,status,publish_attempt,execution_attempt,lease_token,lease_until,last_error_code,finished_at FROM task_intents WHERE request_id LIKE :r ORDER BY id',{'r':ctx['run_id']+'-%'})
    # Notification correlation is its authoritative notification ID, not the
    # originating HTTP request; join only this run's explicit work-order IDs.
    ids=tuple(context()['orders'].values())
    if ids:
        from sqlalchemy import bindparam
        query=text("SELECT i.id,i.operation,i.resource_id,i.resource_version,i.generation,i.task_id,i.status,i.publish_attempt,i.execution_attempt,i.lease_token,i.lease_until,i.last_error_code,i.finished_at FROM task_intents i JOIN notification_deliveries d ON i.operation='DELIVER_NOTIFICATION' AND i.resource_id=d.id JOIN notifications n ON n.id=d.notification_id WHERE n.resource_type='WORK_ORDER' AND n.resource_id IN :ids ORDER BY i.id").bindparams(bindparam('ids',expanding=True))
        with engine.connect() as conn:
            conn.execute(text('SET TRANSACTION READ ONLY'))
            selected.extend(dict(r) for r in conn.execute(query,{'ids':ids}).mappings())
    return sorted(selected,key=lambda r:r['id'])
def refresh_ids():
    result=intents();(PRIVATE/'intent-ids.json').write_text(json.dumps([r['id'] for r in result]),encoding='utf-8');return result
def work_id(label):return context()['orders'][label]
def intent(label):return next(r for r in intents() if r['operation']=='ANALYZE_WORK_ORDER' and r['resource_id']==work_id(label))
def remember(label):
    found=rows('SELECT DISTINCT work_order_id AS id FROM work_order_logs WHERE request_id=:r',{'r':ctx['run_id']+'-case-'+label})
    assert len(found)==1
    current=context();current['orders'][label]=found[0]['id'];assert len(current['orders'])<=3;save_context(current);refresh_ids()
    return found[0]['id']
def create(label):
    current=context();assert label in ('one','two','permanent') and len(current['orders'])<3
    house=current['houses'][{'one':'prepare','two':'dispatch','permanent':'commit'}[label]]
    return req('POST','/work-orders',body={'house_id':house,'contact_phone':current['phones']['resident'],'text':current['run_id']+' '+label+'：普通水龙头滴水，请维修组处理，无烟无火花。'},headers={'Idempotency-Key':current['run_id']+'-'+label},label='case-'+label)
def state(label):return req('GET',f'/work-orders/{work_id(label)}',label='detail-'+label)
def evidence(label):
    oid=work_id(label);data={'id':oid,'intent':intent(label)}
    for key,sql in {
        'work':'SELECT id,status,version,current_assignment_id FROM work_orders WHERE id=:id',
        'analysis':'SELECT id,analysis_version,status,input_hash,error_code FROM work_order_analyses WHERE work_order_id=:id ORDER BY id',
        'assignments':'SELECT id,employee_id,status,assigned_source,assigned_by_user_id FROM work_order_assignments WHERE work_order_id=:id ORDER BY id',
        'calls':'SELECT id,provider_type,attempt,status,model_version FROM ai_call_logs WHERE work_order_id=:id ORDER BY id',
        'logs':'SELECT id,event_code,payload_json FROM work_order_logs WHERE work_order_id=:id ORDER BY id',
    }.items(): data[key]=rows(sql,{'id':oid})
    data['employee_load']=rows('SELECT business_date,current_load,on_duty FROM employee_daily_statuses WHERE employee_id=:id ORDER BY business_date',{'id':context()['employee_id']})
    for r in data['logs']:
        if isinstance(r['payload_json'],str):r['payload_json']=json.loads(r['payload_json'])
    # No contact/text in public SQL; check snapshot privately as boolean.
    phone=rows('SELECT contact_phone_e164 FROM work_orders WHERE id=:id',{'id':oid})[0]['contact_phone_e164']
    data['contact_snapshot_matches']=phone=='+86'+context()['phones']['resident'].removeprefix('+86')
    return data
def assigned(label):
    refresh_ids()
    return state(label)['work_order']['status']=='ASSIGNED'
def finish(label):
    d=state(label);oid=work_id(label)
    for status,action in [('ASSIGNED','accept'),('ACCEPTED','start')]:
        if d['work_order']['status']==status:
            req('POST',f'/staff/work-orders/{oid}/{action}',actor='staff',body={'version':d['work_order']['version']},label=label+'-'+action);d=state(label)
    if d['work_order']['status']=='PROCESSING':req('POST',f'/staff/work-orders/{oid}/receipts',actor='staff',body={'version':d['work_order']['version'],'problem_type':'普通维修','method':'026合成记录：修复滴水并确认可用。','resolved':True},label=label+'-receipt')
    d=state(label)
    if d['work_order']['status']=='WAIT_CONFIRM':req('POST',f'/work-orders/{oid}/confirm',body={'version':d['work_order']['version']},label=label+'-confirm')
    assert state(label)['work_order']['status']=='CLOSED';refresh_ids()
def async_create_kill(label,window,api_label):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(create,label)
        observed=checkpoint(window)
        terminated=kill(api_label)
        try: result=future.result(timeout=5); response='response_received'
        except Exception as exc:response=type(exc).__name__
    return {'checkpoint':observed,'termination':terminated,'client_outcome':response}
def step(key,fn):
    if key in e['steps']:return
    started=time.monotonic();result=fn();elapsed=time.monotonic()-started
    assert elapsed<=120
    e['steps'][key]={**result,'seconds':round(elapsed,3)};save();print(json.dumps({'window':key,'result':'evidence_saved','seconds':round(elapsed,2)}),flush=True)

def precommit():
    control('w1','api','before_business_commit')
    result=async_create_kill('one','w1','api01')
    assert not intents() and not rows('SELECT id FROM work_order_logs WHERE request_id=:r',{'r':ctx['run_id']+'-case-one'})
    result['no_committed_work_or_intent']=True;clear();start('api','api02');wait(api_ready)
    return result
def commit_before_publish():
    control('w2','api','committed_before_publish')
    result=async_create_kill('one','w2','api02')
    oid=remember('one');i=intent('one');assert i['status']=='PENDING' and i['publish_attempt']==0
    result.update(work_order_id=oid,committed_intent=i);clear();start('api','api03');wait(api_ready)
    return result
def confirmed_before_writeback():
    control('w3','publisher','published_before_writeback',intent('one')['id'])
    start('publisher','publisher01');observed=checkpoint('w3');before=intent('one');assert before['status']=='PUBLISHING'
    killed=kill('publisher01');clear();start('publisher','publisher02')
    after=wait(lambda:(i if (i:=intent('one'))['publish_attempt']>=2 else None))
    assert before['task_id']==after['task_id']
    return {'checkpoint':observed,'termination':killed,'before':before,'automatic_republished':after}
def worker_claimed():
    control('w4','worker','claimed_before_domain',intent('one')['id'])
    start('worker','worker01');observed=checkpoint('w4');old=intent('one');killed=kill('worker01')
    clear();start('worker','worker02');wait(lambda:assigned('one'))
    after=evidence('one');assert len(after['assignments'])==1 and after['assignments'][0]['assigned_source']=='AUTO'
    assert len(after['calls'])==1 and after['intent']['execution_attempt']==2
    with Session(engine,expire_on_commit=False) as session:
        stale={'id':old['id'],'token':old['lease_token']}
        rejected=not TaskIntentService(session).finish(stale,'SUCCEEDED')
    assert rejected
    return {'checkpoint':observed,'termination':killed,'old_claim':old,'recovered':after,'old_token_rejected':True,'token_check_mode':'direct CAS negative, not recovery operation'}
def result_before_ack():
    finish('one');control('w5','worker','domain_committed_before_intent_finish')
    create('two');remember('two');observed=checkpoint('w5');before=evidence('two')
    assert before['work'][0]['status']=='ASSIGNED' and len(before['assignments'])==1
    killed=kill('worker02');clear();start('worker','worker03')
    wait(lambda:(i:=intent('two'))['status']=='SUCCEEDED')
    after=evidence('two');assert before['assignments']==after['assignments'] and before['calls']==after['calls']
    assert after['intent']['execution_attempt']==1
    return {'checkpoint':observed,'termination':killed,'before':before,'after':after,'business_committed_before_intent_finish_and_ACK':True}
def permanent_and_knowledge():
    finish('two');create('permanent');remember('permanent')
    wait(lambda:intent('permanent')['status']=='FAILED')
    failed=evidence('permanent');assert failed['intent']['execution_attempt']==3 and not failed['assignments']
    assert any(r['event_code']=='AI_TASK_FAILED' for r in failed['logs'])
    # One initial document version, not a second ingest engine or direct SQL.
    d=req('POST','/admin/knowledge-documents',actor='manager',body={'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id'],'title':ctx['run_id']+'恢复说明','document_type':'GUIDE','visibility':'RESIDENT','source_text':ctx['run_id']+'：合成住户发现水龙头滴水，通过正常报修等待维修人员联系；本说明仅用于隔离测试。'},label='knowledge')
    current=context();current['knowledge']=d;save_context(current);refresh_ids()
    def knowledge_done():
        refresh_ids();records=[i for i in intents() if i['operation']=='INGEST_KNOWLEDGE'];return records and records[0]['status']=='SUCCEEDED'
    wait(knowledge_done)
    refresh_ids();wait(lambda:all(i['status'] in ('SUCCEEDED','FAILED','CANCELLED') for i in refresh_ids()))
    return {'permanent':failed,'knowledge':d,'all_intents':intents(),'notification_delivery':'real Development Provider, no external exactly-once claim'}

def notification_finish():
    refresh_ids()
    wait(lambda:all(i['status'] in ('SUCCEEDED','FAILED','CANCELLED') for i in refresh_ids()))
    deliveries=[i for i in intents() if i['operation']=='DELIVER_NOTIFICATION']
    assert deliveries and all(i['status']=='SUCCEEDED' for i in deliveries)
    return {'intents':deliveries,'correction':'Initial collector selected request marker only, omitting notification-ID-correlated intents. Added exact authoritative work-order join; no new business, no history scan or manual send.'}

try:
    if 'drill_started' not in context():
        current=context();current['drill_started']=stamp();save_context(current)
    budget()
    phase=sys.argv[1]
    functions={'w1':precommit,'w2':commit_before_publish,'w3':confirmed_before_writeback,'w4':worker_claimed,'w5':result_before_ack,'end':permanent_and_knowledge,'notifications':notification_finish}
    if phase=='all':
        for name,fn in functions.items():step(name,fn)
    else:step(phase,functions[phase])
except Exception as exc:
    e.setdefault('failures',[]).append({'utc':stamp(),'phase':sys.argv[1],'error_type':type(exc).__name__,'message':str(exc) if type(exc) in (RuntimeError,AssertionError) else 'Details retained in private runtime logs'})
    raise
finally:
    save();engine.dispose();c.close()
