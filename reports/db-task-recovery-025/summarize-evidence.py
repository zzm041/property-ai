"""Derive timeline only from already saved, scrubbed logs; no runtime access."""
import json
from pathlib import Path
folder=Path(__file__).parent
timeline=[]
for filename in ('worker-01.stdout.txt','worker-02.stdout.txt'):
    for number,line in enumerate((folder/filename).read_text(encoding='utf-8').splitlines(),1):
        try:outer=json.loads(line)
        except (ValueError,TypeError):continue
        if outer.get('operation')!='app.worker.tasks.analyze_work_order_task':continue
        inner=outer.get('event','')
        try:decoded=json.loads(inner)
        except (ValueError,TypeError):decoded=None
        if isinstance(decoded,dict):
            timeline.append({'file':filename,'line':number,'work_order_id':outer.get('work_order_id'),'request_id':outer.get('request_id'),**decoded})
        elif inner in ('ai_analysis_transaction_retry','analysis_database_retries_exhausted','failed_task_recorded') or 'succeeded in' in inner:
            timeline.append({'file':filename,'line':number,'source':'actual product/Celery log','work_order_id':outer.get('work_order_id'),
                'task_id':outer.get('task_id'),'request_id':outer.get('request_id'),'event':inner,'utc':outer.get('timestamp'),
                **{k:outer[k] for k in ('mysql_error_code','attempt','db_persisted','error_code','redis_ok','dlq_ok') if k in outer}})
(folder/'task-timeline.json').write_text(json.dumps({'source':'derived from saved logs; event timestamps are original, no local receive-time inference','events':timeline},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'timeline_entries':len(timeline),'runtime_or_model_calls':0}))
