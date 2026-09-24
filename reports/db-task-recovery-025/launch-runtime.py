"""Development-only launch; --controlled-worker confines faults to new025 run.

Test controls are outside product code: one connection SELECT SLEEP(6) (real
2013), synthetic errors at named boundaries, and bounded availability selection.
Never install controls into a general/production worker.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).parent))
from common import *

p=argparse.ArgumentParser();p.add_argument('component',choices=['api','worker']);p.add_argument('--controlled-worker',action='store_true');a=p.parse_args()
for name in ('LLM','VLM','ASR','EMBEDDING','RERANKER','NOTIFICATION'):os.environ[name+'_PROVIDER']='development'
os.environ['LLM_PROVIDER_BEHAVIOR']='success'
s=settings();apply_isolated_environ(s)
print(json.dumps({'task_id':'DB-TASK-RECOVERY-025','component':a.component,'source_sha256':source_hash(),
 'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'pid':os.getpid(),'python':sys.executable,'module_root':str(ROOT/'backend/app'),
 'database':'127.0.0.1:3307/property_ai_auth001_test@0011','vhost':'property_ai_auth001',
 'queue':'property_ai.auth001','providers':'Development only','controlled_worker':a.controlled_worker}),flush=True)
os.chdir(ROOT/'backend')
if a.component=='api':
    import uvicorn
    from app.main import create_app
    uvicorn.run(create_app(s),host='127.0.0.1',port=8083,log_level='warning')
else:
    from app.worker.celery_app import celery_app
    from app.worker import tasks
    from celery import current_task
    from celery.signals import task_prerun,task_postrun,task_retry
    def marker(sender=None,task_id=None,signal=None,**kw):
        req=getattr(sender,'request',None)
        print(json.dumps({'source':'real Celery signal','event':signal.name,'task_id':task_id or getattr(req,'id',None),
            'operation':sender.name,'attempt':int(getattr(req,'retries',0))+1,'utc':stamp()}),flush=True)
    for signal in (task_prerun,task_postrun,task_retry):signal.connect(marker,weak=False)
    if a.controlled_worker:
        ctx=read_context();run=ctx['run_id'];start=time.monotonic();seen=set()
        from sqlalchemy import event,Engine
        from sqlalchemy.exc import OperationalError
        from pymysql.err import OperationalError as MysqlError
        from app.service.analysis import AnalysisService
        from app.repository.ai import AiRepository
        from app.service.sla import SlaService
        def task_case():
            req=getattr(current_task,'request',None);args=getattr(req,'args',None) or ()
            if getattr(current_task,'name','')!=tasks.analyze_work_order_task.name or len(args)<2 or not str(args[1]).startswith(run+'-case-'):return None
            assert time.monotonic()-start<900,'025 control runtime budget exceeded'
            return str(args[1]).split('-case-',1)[1],int(req.retries),int(args[0])
        def fault(case,stage,real=False):
            print(json.dumps({'source':'025 explicit test control','case':case,'stage':stage,'utc':stamp(),
                'fault':'real dedicated-connection SELECT SLEEP(6) read timeout' if real else 'synthetic2013 at recorded operation boundary'}),flush=True)
        @event.listens_for(Engine,'before_cursor_execute')
        def inject(conn,cursor,statement,parameters,context,executemany):
            case=task_case()
            if not case:return
            name,retries,oid=case
            key=(name,retries)
            sql=statement.lower()
            if name in {'cancel','persistent'} and key not in seen and ((name=='cancel' and retries==0) or name=='persistent') and 'from work_orders' in sql and 'for update' in sql:
                seen.add(key);fault(case,'before-order-lock')
                if name=='cancel':time.sleep(4)  # no row locked; normal cancel API can win
                raise OperationalError(statement,{},MysqlError(2013,'025 controlled operation failure'),connection_invalidated=True)
        @event.listens_for(Engine,'do_execute')
        def driver_timeout(cursor,statement,parameters,context):
            # Raising inside the dialect execute boundary lets SQLAlchemy
            # invalidate and wrap the genuine PyMySQL2013 before rollback.
            case=task_case()
            if not case:return
            name,retries,oid=case;key=(name,retries);sql=statement.lower()
            prepare=name=='commit' and retries==0 and sql.startswith('insert into work_order_analyses')
            dispatch=name=='dispatch' and retries==0 and 'from employees' in sql and 'for update' in sql
            if key not in seen and (prepare or dispatch):
                seen.add(key);fault(case,'prepare-insert' if prepare else 'employee-reservation',True)
                cursor.execute('SELECT SLEEP(6)')
        original_apply=AnalysisService._apply
        def uncertain_apply(self,**kwargs):
            result=original_apply(self,**kwargs)
            case=task_case()
            if case and case[0]=='commit' and case[1]==1 and ('commit',1) not in seen:
                seen.add(('commit',1));fault(case,'after-acknowledged-apply-commit-simulate-lost-ack')
                raise OperationalError('COMMIT',{},MysqlError(2013,'025 simulated lost commit acknowledgement'),connection_invalidated=True)
            return result
        AnalysisService._apply=uncertain_apply
        # Narrow only selection, retaining production sweep/dispatch logic. No
        # old-object scans or explicit analyze/assign calls from an API script.
        original_waiting=AiRepository.list_waiting_dispatch
        def scoped_waiting(self,**kwargs):
            latest=read_context();ids=list(latest.get('orders',{}).values())
            if not ids:return ()
            return original_waiting(self,**{**kwargs,'limit':6,'work_order_ids':tuple(ids)})
        AiRepository.list_waiting_dispatch=scoped_waiting
        SlaService.sweep_unavailable_assignments=lambda self,**kwargs:0
        print(json.dumps({'source':'025 test controls','run_id':run,'max_new_orders':6,'max_fault_seconds':8,
                          'max_total_seconds':900,'scope':'no beat; availability selection only newrun IDs; no historical message access'}),flush=True)
    celery_app.worker_main(['worker','-Q','property_ai.auth001','--pool=solo','--loglevel=info','--hostname=auth001@%h'])
