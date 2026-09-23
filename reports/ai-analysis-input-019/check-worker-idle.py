"""Check only named AUTH001 worker/vhost readiness before a controlled replacement."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
settings = h['_guard']()
from auth001_isolated_runtime import apply_isolated_environ
apply_isolated_environ(settings)
from app.worker.celery_app import celery_app

worker = 'auth001@DESKTOP-8NHSE12'
inspection = celery_app.control.inspect(destination=[worker], timeout=8)
counts = {}
for name in ('active', 'reserved', 'scheduled'):
    result = getattr(inspection, name)()
    assert result is not None and worker in result, 'named worker did not answer'
    counts[name] = len(result[worker])
    assert counts[name] == 0, 'wait for active work; do not stop'
q = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
assert q.returncode == 0
row = next(line.split() for line in q.stdout.splitlines() if line.startswith('property_ai.auth001\t'))
assert row[1:] == ['0','0','1']
public = {'task_id':'AI-ANALYSIS-INPUT-019','observed_at_utc':datetime.now(UTC).isoformat(),
          'target_worker':worker,'counts':counts,'vhost':'property_ai_auth001','queue':'property_ai.auth001',
          'actual_queue_output':q.stdout,'shared_vhost_accessed':False,'message_content_read':False}
filename = sys.argv[1] if len(sys.argv) > 1 else 'pre-replacement-idle.json'
assert filename in {'pre-replacement-idle.json', 'fix1-pre-replacement-idle.json'}
destination = Path(__file__).with_name(filename)
assert not destination.exists(), 'Preserve saved idle evidence; do not overwrite'
destination.write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(public))
