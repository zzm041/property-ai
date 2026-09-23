"""Scoped API negatives plus SELECT-only DB/queue metadata; never replay order12."""
from pathlib import Path
from datetime import UTC,datetime
import hashlib,json,runpy,subprocess
ROOT=Path(r'D:\property-ai');OUT=Path(__file__).parent
DEST=OUT/'continuation-api-db.json'
if DEST.exists():
    prior=json.loads(DEST.read_text(encoding='utf-8'))
    assert prior['status']=='INCOMPLETE'
    if len(prior['requests'])==1 and prior['requests'][0]['status']==401:
        archive=OUT/'continuation-api-expired-session.json';assert not archive.exists()
        archive.write_bytes(DEST.read_bytes())
        prior=None
    else:
        archive=OUT/'continuation-api-before-denial-resume.json';assert not archive.exists()
        archive.write_bytes(DEST.read_bytes())
else:prior=None
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
import httpx
from sqlalchemy import create_engine,text
settings=h['_guard']();ctx=json.loads((ROOT/'tmp/resident-house-select-023/continuation-context.json').read_text(encoding='utf-8'))
before=json.loads((OUT/'continuation-preflight.json').read_text(encoding='utf-8'))
e={'task_id':'RESIDENT-HOUSE-SELECT-023','run_id':ctx['run_id'],'code_sha':ctx['code_sha'],'at_utc':datetime.now(UTC).isoformat(),'requests':[],'successful_business_writes':False}
if prior:
    e=prior;e['collector_resume_note']='No passed request repeated. Existing WorkOrder read policy returns403 for unauthorized same-property resident, not assumed404; no data body disclosed. Continue from saved denial.'
def save():DEST.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
client=httpx.Client(base_url='http://127.0.0.1:8083/api/v1',trust_env=False,timeout=15)
engine=create_engine(settings.test_database_url)
def call(label,path,identity=None,params=None,body=None,expected=(200,)):
    previous=next((r for r in e['requests'] if r['label']==label),None)
    if previous:
        assert previous['status'] in expected,(label,previous['status'])
        return previous['response'].get('data')
    headers={'X-Request-ID':ctx['run_id']+'-'+label}
    if identity:headers['Authorization']='Bearer '+ctx['tokens'][identity]
    if body:headers['Idempotency-Key']=ctx['run_id']+'-'+label
    r=client.request('POST' if body else 'GET',path,params=params,json=body,headers=headers);p=r.json()
    e['requests'].append({'label':label,'method':r.request.method,'path':path,'params':params,'body':body,'status':r.status_code,'response':p});save()
    assert r.status_code in expected,(label,r.status_code)
    return p.get('data')
try:
    # Earlier page access tokens expired while other evidence was inspected.
    # Authenticate normally once per synthetic identity; never publish credentials.
    auth=h['Client'](ctx['run_id']+'-api');auth.http.close();auth.http=httpx.Client(base_url=h['API'],trust_env=False,timeout=15)
    try:
        if not e.get('normal_auth_requests'):
            for identity in ('multi','empty'):
                data=auth.sms_login(ctx['phones'][identity],identity)
                ctx['tokens'][identity]=data['access_token']
            (ROOT/'tmp/resident-house-select-023/continuation-context.json').write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
            e['normal_auth_requests']=auth.requests
    finally:auth.close()
    for label in ('multi','empty'):
        me=call(label+'-principal','/me',label);assert me['user_id']==ctx['identities'][label] and 'SYSTEM_ADMIN' not in me['roles']
    rows=[];cursor=None;pages=[]
    for n in range(4):
        params={'limit':20}
        if cursor is not None:params['after_id']=cursor
        page=call('page-'+str(n),'/me/houses','multi',params=params);rows.extend(page['items']);pages.append(len(page['items']));cursor=page['next_cursor']
        if not cursor:break
    assert pages==[20,20,11]
    assert [r['relation_id'] for r in rows]==[r['relation_id'] for r in ctx['expected_relations']['multi']]
    for actual,want in zip(rows,ctx['expected_relations']['multi']):
        assert all(actual[k]==want[k] for k in ('house_id','property_name','building_name','unit_name','room_no'))
    e['pagination']={'counts':pages,'unique_relations':len({r['relation_id'] for r in rows}),'sql_exact_match':True}
    end=call('after-last','/me/houses','multi',params={'limit':20,'after_id':rows[-1]['relation_id']});assert end['items']==[] and end['next_cursor'] is None
    for limit in (0,101):call('invalid-limit-'+str(limit),'/me/houses','multi',params={'limit':limit},expected=(422,))
    call('unauthenticated','/me/houses',expected=(401,))
    empty=call('empty-cannot-impersonate','/me/houses','empty',params={'user_id':ctx['identities']['multi']});assert empty['items']==[]
    for identity in ('multi','empty'):
        call(identity+'-foreign-order','/work-orders/'+str(ctx['work_order_id']),identity,expected=(403,404))
    # Intentionally invalid house ownership; failure must precede any event/task creation.
    for identity in ('multi','empty'):
        call(identity+'-foreign-create','/work-orders',identity,body={'house_id':ctx['house_id'],'text':ctx['run_id']+' denied foreign-house probe'},expected=(403,404))
    call('forged-property','/work-orders','multi',body={'house_id':ctx['foreign_house_id'],'property_id':2147483647,'text':ctx['run_id']+' denied scope probe'},expected=(403,404,422))
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one();assert e['revision']=='20260919_0009'
        e['orders']=[dict(r) for r in c.execute(text('SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id FROM work_orders ORDER BY id')).mappings()]
        assert e['orders']==before['orders']
        e['work_order_row_ids']={table:list(c.execute(text(f'SELECT id FROM {table} WHERE work_order_id=:id ORDER BY id'),{'id':ctx['work_order_id']}).scalars()) for table in ('work_order_analyses','ai_call_logs','work_order_assignments','work_order_logs')}
        assert e['work_order_row_ids']==before['work_order_row_ids']
    q=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,check=True,timeout=30)
    e['queue']={'vhost':'property_ai_auth001','metadata_only':q.stdout,'message_contents_read':False}
    assert next(row.split() for row in q.stdout.splitlines() if row.startswith('property_ai.auth001\t'))[1:]==['0','0','1']
    e['project_config_sha256']=hashlib.sha256((ROOT/'miniprogram/project.config.json').read_bytes()).hexdigest().upper();assert e['project_config_sha256']==before['project_config_sha256']
    e['status']='SCOPED_API_DB_CHECKS_PASSED';e.pop('error_type',None);save();print(json.dumps({'status':e['status'],'pagination':pages,'requests':len(e['requests']),'order12_unchanged':True}))
except Exception as exc:
    e['status']='INCOMPLETE';e['error_type']=type(exc).__name__;save();raise
finally:client.close();engine.dispose()
