"""Dedicated run controls; no model, shared services, old task scan or Beat."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
p=argparse.ArgumentParser();p.add_argument('component',choices=['api','worker','publisher']);p.add_argument('--run-id',required=True);p.add_argument('--label',required=True);a=p.parse_args()
ctx=context();assert a.run_id==ctx['run_id']
for k in ('LLM','VLM','ASR','EMBEDDING','RERANKER','NOTIFICATION'):os.environ[k+'_PROVIDER']='development'
os.environ['LLM_PROVIDER_BEHAVIOR']='success'
s=settings();apply_isolated_environ(s);os.environ['DURABLE_TASKS_ENABLED']='true';guard(s)
from app.service.task_intent import IntentTiming
timing=IntentTiming(5,8,5,1,60)  # Dedicated test only; product defaults unchanged.
control_path=PRIVATE/'control.json'
def control():return json.loads(control_path.read_text(encoding='utf-8')) if control_path.exists() else {}
def checkpoint(stage,intent_id):
    current=control()
    if current.get('stage')!=stage or current.get('component')!=a.component:return
    if current.get('intent_id') not in (None,intent_id):return
    if a.component=='worker':
        engine=create_database_engine(s.active_database_url)
        with engine.connect() as conn:
            op=conn.execute(text('SELECT operation FROM task_intents WHERE id=:id'),{'id':intent_id}).scalar_one()
        engine.dispose()
        if op!='ANALYZE_WORK_ORDER':return
    marker=PRIVATE/(current['window']+'-checkpoint.json')
    if marker.exists():return
    with marker.open('x',encoding='utf-8') as f:json.dump({'stage':stage,'intent_id':intent_id,'pid':os.getpid(),'run_id':a.run_id,'utc':stamp()},f)
    print(json.dumps({'source':'explicit026testcontrol','stage':stage,'intent_id':intent_id,'utc':stamp()}),flush=True)
    until=time.monotonic()+110
    while time.monotonic()<until:time.sleep(.2)
    raise RuntimeError('026 checkpoint timed out; no silent continuation')
print(json.dumps({'task_id':'RELIABLE-TASK-DELIVERY-026','component':a.component,'label':a.label,'run_id':a.run_id,'pid':os.getpid(),'python':sys.executable,'source_sha256':source_hash(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'database':'127.0.0.1:3307/property_ai_auth001_test@0012','vhost':'property_ai_auth001','queue':'property_ai.auth001','provider':'Development','durable_tasks_enabled':True,'test_timing':timing.__dict__,'utc':stamp()}),flush=True)
if a.component=='api':
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.model.task_intent import TaskIntent
    from app.service import task_intent as intent_service
    original_register=intent_service.register_intent
    def register(session,**kw):
        result=original_register(session,**kw)
        session.info.setdefault('026-new-intents',[]).append(result)
        return result
    intent_service.register_intent=register
    @event.listens_for(Session,'before_commit')
    def before_commit(session):
        current=control()
        if current.get('stage')!='before_business_commit':return
        for obj in session.info.get('026-new-intents',[]):
            if isinstance(obj,TaskIntent) and obj.operation=='ANALYZE_WORK_ORDER' and obj.request_id.startswith(a.run_id):
                checkpoint('before_business_commit',obj.id);break
    from app.service.work_order import WorkOrderService
    original=WorkOrderService._enqueue_analysis
    def after_commit(self,work_order_id,request_id,*,version=None):
        if request_id.startswith(a.run_id):checkpoint('committed_before_publish',work_order_id)
        return original(self,work_order_id,request_id,version=version)
    WorkOrderService._enqueue_analysis=after_commit
    import uvicorn
    from app.main import create_app
    uvicorn.run(create_app(s),host='127.0.0.1',port=8083,log_level='warning',access_log=False)
elif a.component=='publisher':
    from app.worker.task_intents import run_publisher
    def ids():
        value=json.loads((PRIVATE/'intent-ids.json').read_text(encoding='utf-8'))
        assert isinstance(value,list) and all(type(i) is int and i>0 for i in value)
        return tuple(value)
    engine=create_database_engine(s.active_database_url)
    remaining=900-(datetime.now(UTC)-datetime.fromisoformat(context()['drill_started'])).total_seconds()
    try:run_publisher(engine,s,ids_supplier=ids,seconds=max(.01,remaining),timing=timing,checkpoint=checkpoint)
    finally:engine.dispose()
else:
    from app.worker import task_intents
    original=task_intents.execute_intent
    def execute(engine,settings,intent_id,task_id,**kw):
        assert intent_id in json.loads((PRIVATE/'intent-ids.json').read_text(encoding='utf-8')), 'No unapproved old intent'
        return original(engine,settings,intent_id,task_id,timing=timing,checkpoint=checkpoint)
    task_intents.execute_intent=execute
    domain=task_intents._run_domain
    def controlled_domain(session,settings,lease):
        if lease['request_id'].endswith('-case-permanent'):
            from app.provider.errors import ProviderUnavailableError
            raise ProviderUnavailableError('Controlled026 permanent availability failure')
        return domain(session,settings,lease)
    task_intents._run_domain=controlled_domain
    # Availability dispatch from normal notifications/receipts must remain new-run scoped.
    from app.repository.ai import AiRepository
    from app.service.sla import SlaService
    oldwaiting=AiRepository.list_waiting_dispatch
    def waiting(self,**kw):
        ids=tuple(context().get('orders',{}).values())
        return oldwaiting(self,**{**kw,'work_order_ids':ids,'limit':3}) if ids else ()
    AiRepository.list_waiting_dispatch=waiting
    SlaService.sweep_unavailable_assignments=lambda self,**kw:0
    task_intents.celery_app.worker_main(['worker','-Q','property_ai.auth001','--pool=solo','--loglevel=warning','--hostname=auth001@%h','--without-mingle','--without-gossip'])
