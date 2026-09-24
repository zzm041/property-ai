"""Bind exact026WIP and retained results without any service/test operations."""
from pathlib import Path
import sys
import hashlib
import json
import subprocess
ROOT=Path(__file__).resolve().parents[2]
paths=[
 'backend/alembic/versions/20260925_0012_task_intents.py',
 'backend/app/model/task_intent.py','backend/app/model/__init__.py',
 'backend/app/repository/task_intent.py','backend/app/service/task_intent.py',
 'backend/app/worker/task_intents.py','backend/app/api/v1/task_intents.py',
 'backend/app/api/v1/router.py','backend/app/api/v1/admin_knowledge.py',
 'backend/app/core/config.py','backend/app/domain/audit.py',
 'backend/app/service/work_order.py','backend/app/service/analysis.py',
 'backend/app/service/knowledge.py','backend/app/service/notification.py',
 'backend/app/service/sla.py','backend/app/service/incident.py',
 'backend/app/worker/celery_app.py','backend/app/worker/enqueue.py',
 'backend/tests/unit/test_task_intents_026.py','backend/tests/unit/test_database_recovery_025.py',
 'backend/tests/unit/test_knowledge_management.py','scripts/ops/publish_task_intents.py',
 'docs/03-DOMAIN-MODEL.md','docs/06-API-CONTRACT.md']
record={'task_id':'RELIABLE-TASK-DELIVERY-026','product_base':'7bc504bd6fe287075fd7b4cd14d08ab944d70bfd',
        'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'files':{p:hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in paths},
        'hash_mode':'LF canonical; preexisting ai.py EOL/config/oldreport changes excluded',
        'runtime_boundary':'fivewindow source is runtime-final.json source_sha256; final management recovery additions only scoped tests, no live generation replay',
        'latest_tests':'implementation-tests-r1-commit-ack-reconcile.txt'}
name='review-fix1-manifest.json' if '--fix1' in sys.argv else 'review-input-manifest.json'
with (Path(__file__).parent/name).open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
print(json.dumps({'files':len(paths),'head':record['head']}))
