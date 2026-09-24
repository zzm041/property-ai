"""Metadata only, AUTH001 control channel only; never read task payloads."""
import json
from pathlib import Path
import runpy
import sys

stop = '--shutdown-idle' in sys.argv
sys.argv = [sys.argv[0], 'worker', '--check-only']
runpy.run_path(str(Path(__file__).with_name('launch-runtime.py')), run_name='__main__')
from app.worker.celery_app import celery_app

target = 'auth001@DESKTOP-8NHSE12'
inspect = celery_app.control.inspect(destination=[target], timeout=5)
counts = {}
for key in ('active', 'reserved', 'scheduled'):
    result = getattr(inspect, key)()
    assert result is not None and target in result, 'No authoritative worker reply'
    counts[key] = len(result[target])
print(json.dumps({'worker': target, 'task_counts': counts}), flush=True)
if stop:
    assert not any(counts.values()), 'Worker is not idle'
    celery_app.control.broadcast('shutdown', destination=[target])
    print('AUTH001 idle worker graceful shutdown requested', flush=True)
