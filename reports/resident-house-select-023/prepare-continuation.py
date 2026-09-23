"""Read-only preflight; reuse actual synthetic identities and existing houses."""
from datetime import UTC, datetime
from pathlib import Path
import hashlib
import json
import runpy
import secrets
import subprocess
import urllib.request

ROOT=Path(r'D:\property-ai')
OUT=Path(__file__).parent
PRIVATE=ROOT/'tmp/resident-house-select-023/continuation-context.json'
assert not PRIVATE.exists() and not (OUT/'continuation-preflight.json').exists()
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine,text
settings=h['_guard']()
old=json.loads((ROOT/'tmp/selfhost-ai-local/business-context.json').read_text(encoding='utf-8'))
many=json.loads((ROOT/'tmp/house-catalog-paging-016/context.json').read_text(encoding='utf-8'))
ctx={'run_id':'house023-'+secrets.token_hex(6),'code_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
     'phones':{'primary':old['phones']['resident'],'multi':many['phones']['resident'],'empty':many['phones']['other']},
     'identities':{'primary':old['identities']['resident'],'multi':many['identities']['resident'],'empty':many['identities']['other']},
     'tenant_id':old['tenant_id'],'property_id':old['property_id'],'work_order_id':old['work_order_id'],'original_run_id':old['run_id'],
     'last_sms_sent_at':{},'tokens':{}}
e={'task_id':'RESIDENT-HOUSE-SELECT-023','run_id':ctx['run_id'],'code_sha':ctx['code_sha'],'at_utc':datetime.now(UTC).isoformat(),
   'database':'127.0.0.1:3307/property_ai_auth001_test','business_mutations':False,'identities':ctx['identities'],'relations':{}}
e['collector_note']='Initial read-only invocation reached queue metadata then Docker access failed; no fixture/auth/business request or checkpoint write occurred. Retried with approved host read access.'
engine=create_engine(settings.test_database_url)
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert e['revision']=='20260919_0009'
        for label,user in ctx['identities'].items():
            e['relations'][label]=[dict(r) for r in c.execute(text('SELECT r.id AS relation_id,r.user_id,r.house_id,r.tenant_id,r.property_id,r.relation_type,p.name AS property_name,p.address AS property_address,b.name AS building_name,u.name AS unit_name,h.room_no FROM resident_house_relations r JOIN houses h ON h.id=r.house_id AND h.tenant_id=r.tenant_id AND h.property_id=r.property_id JOIN units u ON u.id=h.unit_id AND u.tenant_id=h.tenant_id AND u.property_id=h.property_id JOIN buildings b ON b.id=u.building_id AND b.tenant_id=h.tenant_id AND b.property_id=h.property_id JOIN properties p ON p.id=h.property_id AND p.tenant_id=h.tenant_id WHERE r.user_id=:user AND r.status="ACTIVE" AND r.valid_from<=UTC_TIMESTAMP(6) AND (r.valid_to IS NULL OR r.valid_to>UTC_TIMESTAMP(6)) ORDER BY r.id'),{'user':user}).mappings()]
        assert len(e['relations']['primary'])==1 and len(e['relations']['multi'])>20 and not e['relations']['empty']
        ctx['house_id']=e['relations']['primary'][0]['house_id']
        ctx['foreign_house_id']=e['relations']['multi'][-1]['house_id']
        ctx['expected_relations']=e['relations']
        e['orders']=[dict(r) for r in c.execute(text('SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id FROM work_orders ORDER BY id')).mappings()]
        order=next(r for r in e['orders'] if r['id']==ctx['work_order_id'])
        assert order['reporter_user_id']==ctx['identities']['primary'] and order['house_id']==ctx['house_id'] and order['status']=='PENDING_ANALYSIS' and order['version']==1
        e['work_order_row_ids']={table:list(c.execute(text(f'SELECT id FROM {table} WHERE work_order_id=:id ORDER BY id'),{'id':ctx['work_order_id']}).scalars()) for table in ('work_order_analyses','ai_call_logs','work_order_assignments','work_order_logs')}
    q=subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,check=True,timeout=30)
    e['queue']={'vhost':'property_ai_auth001','metadata_only':q.stdout,'message_contents_read':False}
    main=next(row.split() for row in q.stdout.splitlines() if row.startswith('property_ai.auth001\t'))
    assert main[1:]==['0','0','1']
    e['runtime_headers']={name:json.loads((ROOT/f'tmp/selfhost-ai-local/{name}.stdout.log').read_text(encoding='utf-8').splitlines()[0]) for name in ('api','worker')}
    assert e['runtime_headers']['api']['code_sha']==e['runtime_headers']['worker']['code_sha']=='e65b4d7fb0e9541695fdcb9a1f1b216d97dbf6c2'
    e['project_config_sha256']=hashlib.sha256((ROOT/'miniprogram/project.config.json').read_bytes()).hexdigest().upper()
    assert e['project_config_sha256']=='342ED0B61FE9C74E7A5CE82198C8F36BD9341D5CDA83DE2C408F33256867FE1F'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8083/api/v1/healthz',timeout=5) as r:
        e['api_health']={'status':r.status,'body':json.load(r)}
    PRIVATE.parent.mkdir(parents=True,exist_ok=True)
    PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'continuation-preflight.json').write_text(json.dumps(e,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({'run_id':ctx['run_id'],'revision':e['revision'],'relation_counts':{key:len(rows) for key,rows in e['relations'].items()},'old_order_unchanged':True}))
finally:
    engine.dispose()
