"""Bounded named-vhost Worker idle check, no business/task-message consumption."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
helper = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
settings = helper['_guard']()
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
    assert counts[name] == 0, 'wait for current work before replacing worker'
queue = subprocess.run(['docker', 'exec', 'property-ai-rabbitmq-1', 'rabbitmqctl', 'list_queues', '-p',
                        'property_ai_auth001', 'name', 'messages_ready', 'messages_unacknowledged', 'consumers'],
                       capture_output=True, text=True, timeout=30)
assert queue.returncode == 0
row = next(line.split() for line in queue.stdout.splitlines() if line.startswith('property_ai.auth001\t'))
assert row[1:] == ['0', '0', '1'], 'queue not idle'
public = {'task_id': 'MANAGER-WO-CANCEL-017', 'observed_at_utc': datetime.now(UTC).isoformat(),
          'target_worker': worker, 'counts': counts, 'vhost': 'property_ai_auth001', 'queue': 'property_ai.auth001',
          'actual_queue_output': queue.stdout, 'shared_vhost_accessed': False, 'message_content_read': False}
Path(__file__).with_name('pre-replacement-idle.json').write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(public))
