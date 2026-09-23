"""Read-only facts for the one actual page-created order, successful or not."""
from pathlib import Path
from datetime import UTC, datetime
import hashlib
import json
import runpy
import subprocess
import urllib.request

ROOT = Path(r"D:\property-ai")
OUT = Path(__file__).parent
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from auth001_isolated_runtime import apply_isolated_environ

target = OUT / 'business-database-runtime.json'
assert not target.exists(), 'Keep original evidence; use a named continuation if needed'
ctx = json.loads((ROOT/'tmp/selfhost-ai-local/business-context.json').read_text(encoding='utf-8'))
page = json.loads((OUT/'business-page.json').read_text(encoding='utf-8'))
preflight = json.loads((OUT/'business-preflight.json').read_text(encoding='utf-8'))
assert ctx['work_order_id']
assert len([row for row in page['requests'] if row['method']=='POST']) == 1
settings = h['_guard']()
engine = create_engine(settings.test_database_url)
e = {'task_id':'SELFHOST-AI-LOCAL-001','run_id':ctx['run_id'],'code_sha':ctx['code_sha'],
     'database':'127.0.0.1:3307/property_ai_auth001_test','request_id':ctx['create_request_id'],
     'observed_at_utc':datetime.now(UTC).isoformat(),'read_only':True,'page_file_sha256':hashlib.sha256((OUT/'business-page.json').read_bytes()).hexdigest()}
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert e['revision'] == '20260919_0009'
        params = {'id':ctx['work_order_id'],'tenant':ctx['tenant_id'],'property':ctx['property_id']}
        e['work_order'] = dict(c.execute(text('SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id,created_at FROM work_orders WHERE id=:id AND tenant_id=:tenant AND property_id=:property'),params).mappings().one())
        e['analyses'] = [dict(r) for r in c.execute(text('SELECT id,work_order_id,analysis_version,status,category,subcategory,severity,priority,summary,risk,required_role,required_skill,confidence,error_code,input_hash,prompt_version FROM work_order_analyses WHERE work_order_id=:id ORDER BY id'),params).mappings()]
        e['ai_calls'] = [dict(r) for r in c.execute(text('SELECT id,work_order_id,request_id,provider_type,provider_mode,model_version,status,attempt,error_code,latency_ms,token_usage,input_hash,prompt_version,started_at,finished_at FROM ai_call_logs WHERE work_order_id=:id ORDER BY id'),params).mappings()]
        e['assignments'] = [dict(r) for r in c.execute(text('SELECT id,work_order_id,employee_id,assigned_by_user_id,assigned_source,status,reason,payload_json FROM work_order_assignments WHERE work_order_id=:id ORDER BY id'),params).mappings()]
        e['recommendations'] = [dict(r) for r in c.execute(text('SELECT id,analysis_id,employee_id,eligible,selected,filter_reason,reason_codes,score,rank_no FROM dispatch_recommendations WHERE work_order_id=:id ORDER BY id'),params).mappings()]
        e['history'] = [dict(r) for r in c.execute(text('SELECT id,actor_user_id,event_code,from_status,to_status,payload_json,created_at FROM work_order_logs WHERE work_order_id=:id ORDER BY id'),params).mappings()]
        e['audit'] = [dict(r) for r in c.execute(text("SELECT id,actor_user_id,action,resource_type,resource_id,request_id,details_json,created_at FROM audit_logs WHERE resource_type='WORK_ORDER_ANALYSIS' AND resource_id IN (SELECT id FROM work_order_analyses WHERE work_order_id=:id) ORDER BY id"),params).mappings()]
        e['staff_daily'] = [dict(r) for r in c.execute(text('SELECT employee_id,property_id,business_date,on_duty,current_load FROM employee_daily_statuses WHERE property_id=:property ORDER BY employee_id,business_date'),params).mappings()]
        e['prior_orders'] = [dict(r) for r in c.execute(text('SELECT id,status,version FROM work_orders WHERE tenant_id=:tenant AND property_id=:property AND id<>:id ORDER BY id'),params).mappings()]
        assert e['prior_orders'] == preflight['prior_orders']
    e['worker_events'] = []
    for line in (ROOT/'tmp/selfhost-ai-local/worker.stdout.log').read_text(encoding='utf-8').splitlines():
        try: row=json.loads(line)
        except ValueError: continue
        if row.get('request_id') == ctx['create_request_id']:
            e['worker_events'].append({k:row[k] for k in ('timestamp','event','task_id','request_id','work_order_id','provider','latency_ms','error_code') if k in row})
    apply_isolated_environ(settings)
    from app.worker.celery_app import celery_app
    worker='auth001@DESKTOP-8NHSE12'
    inspector=celery_app.control.inspect(destination=[worker],timeout=8)
    e['idle']={}
    for name in ('active','reserved','scheduled'):
        result=getattr(inspector,name)()
        assert result is not None and worker in result
        e['idle'][name]=len(result[worker])
    queue=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30,check=True)
    e['queue']={'vhost':'property_ai_auth001','output':queue.stdout,'shared_access':False,'message_contents_read':False}
    e['runtime']={name:json.loads((ROOT/f'tmp/selfhost-ai-local/{name}.stdout.log').read_text(encoding='utf-8').splitlines()[0]) for name in ('api','worker')}
    assert e['runtime']['api']['code_sha']==e['runtime']['worker']['code_sha']==ctx['code_sha']
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8083/api/v1/healthz',timeout=5) as response:
        e['api_health']={'status':response.status,'body':json.load(response)}
    e['replacement_helper_limit']='Initial helper used wrong /healthz path; correct prefixed path independently confirms same already-started API; no second replacement.'
    e['status']='REAL_QWEN_AUTO_MECHANISM_CONFIRMED' if e['assignments'] and e['work_order']['status']=='ASSIGNED' else 'REAL_QWEN_CALLED_BUT_AUTO_NOT_ACHIEVED'
finally:
    target.write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    engine.dispose()
print(json.dumps({k:e.get(k) for k in ('status','work_order','analyses','ai_calls','assignments')},ensure_ascii=False,default=str))
