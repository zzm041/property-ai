"""Add one synthetic property via existing fixture service, no state SQL edits.

The first setup reused the historical test property. Availability recovery
correctly found its old ASSIGNED backlog. Preserve those outcomes; move only
this run's newly created employees to a new synthetic scope for isolated tests.
"""
from datetime import UTC, datetime
import json
from pathlib import Path
import runpy
import time

ROOT=Path(__file__).resolve().parents[2]
h=runpy.run_path(str(ROOT/'reports/notification-events-inbox-013/run-runtime.py'))
from auth001_isolated_runtime import build_isolated_settings
from app.core.database import assert_isolated_auth_database, create_database_engine
from app.service.property import PropertyService
from sqlalchemy.orm import Session

private=ROOT/'tmp/maintenance-contact-dispatch-024/context.json'
ctx=json.loads(private.read_text(encoding='utf-8'))
out=Path(__file__).with_name('scope-isolation.json')
e=json.loads(out.read_text(encoding='utf-8')) if out.exists() else {'run_id':ctx['run_id'],'steps':{},'requests':[]}
settings=build_isolated_settings();assert_isolated_auth_database(settings,database_url=settings.active_database_url)
c=h['Client'](ctx['run_id']+'-scope')
def save():
 private.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
 e['requests'].extend(c.requests);c.requests.clear()
 out.write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf-8')
def step(name,fn):
 if name not in e['steps']:e['steps'][name]=fn();save()
 return e['steps'][name]
try:
 me=c.data('GET','/me',token=ctx['tokens']['manager'],label='me')
 assert any(s['role_code']=='TENANT_ADMIN' and s['scope_type']=='TENANT' and s['tenant_id']==ctx['tenant_id'] for s in me['scopes'])
 old_property=ctx.setdefault('initial_property_id',ctx['property_id'])
 if 'property' not in e['steps']:
  engine=create_database_engine(settings.active_database_url)
  with Session(engine,expire_on_commit=False) as session:
   prop=PropertyService(session).create_property(tenant_id=ctx['tenant_id'],name=ctx['run_id']+'隔离维修小区',address='仅限合成软件测试')
   e['steps']['property']={'id':prop.id,'tenant_id':prop.tenant_id,'mode':'existing authorized additive PropertyService fixture; not UI evidence'}
   save()
  engine.dispose()
 ctx['property_id']=e['steps']['property']['id'];save()
 token=ctx['tokens']['manager'];scope={'tenant_id':ctx['tenant_id'],'property_id':ctx['property_id']}
 for actor in ('staff1','staff2'):
  detail=c.data('GET',f"/admin/employees/{ctx['employees'][actor]}",token=token,label=actor+'-detail')
  for assignment in detail['assignments']:
   if assignment['property_id']==old_property and assignment['status']=='ACTIVE':
    step('end-old-'+actor,lambda assignment=assignment,actor=actor:c.data('POST',f"/admin/employees/{ctx['employees'][actor]}/property-assignments/{assignment['id']}/end",token=token,body={},label='end-old-'+actor))
  step('assign-new-'+actor,lambda actor=actor:c.data('POST',f"/admin/employees/{ctx['employees'][actor]}/property-assignments",token=token,body={'property_id':ctx['property_id']},label='assign-new-'+actor))
 for label in ('a','b','c'):
  b=step('building-'+label,lambda label=label:c.data('POST','/admin/buildings',token=token,body={**scope,'code':ctx['run_id']+'-'+label,'name':'024隔离维修'+label},label='building-'+label))
  u=step('unit-'+label,lambda b=b,label=label:c.data('POST','/admin/units',token=token,body={**scope,'building_id':b['id'],'code':'01','name':'合成单元'},label='unit-'+label))
  house=step('house-'+label,lambda u=u,label=label:c.data('POST','/admin/houses',token=token,body={**scope,'unit_id':u['id'],'room_no':'2401','floor':24},label='house-'+label))
  ctx['houses'][label]=house['id'];save()
  req=step('request-'+label,lambda label=label,house=house:c.data('POST','/resident-bind-requests',token=ctx['tokens']['resident'],body={'house_id':house['id'],'relation_type':'TENANT'},label='request-'+label))
  step('approve-'+label,lambda label=label,req=req:c.data('POST',f"/admin/resident-bind-requests/{req['id']}/approve",token=token,body={},label='approve-'+label))
 e['reason']='Earlier qualification event recovered historical non12 backlog; no historical output deleted or state restored. New scope uses same real tenant-admin and business APIs; no role widening.'
 save();print(json.dumps({'run_id':ctx['run_id'],'new_property':ctx['property_id'],'houses':ctx['houses'],'old_scope':old_property}))
finally:save();c.close()
