"""024 software-only Development runtime; not a real-model business switch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import site
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT / '.venv/Lib/site-packages'))
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts/ops')]
from auth001_isolated_runtime import build_isolated_settings, apply_isolated_environ, describe_isolated_target
from app.core.database import assert_isolated_auth_database

parser = argparse.ArgumentParser()
parser.add_argument('component', choices=['api', 'worker'])
parser.add_argument('--check-only', action='store_true')
parser.add_argument('--worker-concurrency', type=int, choices=[1, 2, 4], default=1)
args = parser.parse_args()
for name in ('LLM', 'VLM', 'ASR', 'EMBEDDING', 'RERANKER', 'NOTIFICATION'):
    os.environ[name + '_PROVIDER'] = 'development'
for name in ('LLM', 'ASR'):
    os.environ[name + '_PROVIDER_BEHAVIOR'] = 'success'
settings = build_isolated_settings(enable_enqueue=True)
assert_isolated_auth_database(settings, database_url=settings.active_database_url)
assert urlsplit(settings.celery_broker_url).path == '/property_ai_auth001'
assert settings.celery_task_queue == 'property_ai.auth001'
assert all(getattr(settings, name + '_provider') == 'development' for name in ('llm', 'vlm', 'asr', 'embedding', 'reranker', 'notification'))
assert not settings.dev_auth_enabled
apply_isolated_environ(settings)
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
sha = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
diff = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--no-ext-diff', 'HEAD', '--', 'backend/app'])
print(json.dumps({'task_id': 'MAINTENANCE-CONTACT-DISPATCH-024', 'component': args.component,
                  'head_sha': sha, 'backend_app_wip_diff_sha256': hashlib.sha256(diff).hexdigest(),
                  'python': sys.executable, 'module_root': str(ROOT/'backend/app'),
                  'target': describe_isolated_target(settings), 'prompt_version': WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                  'providers': 'Development only; no real inference', 'check_only': args.check_only}), flush=True)
if not args.check_only:
    os.chdir(ROOT/'backend')
    if args.component == 'api':
        import uvicorn
        from app.main import create_app
        uvicorn.run(create_app(settings), host='127.0.0.1', port=8083, log_level='warning')
    else:
        from app.worker.celery_app import celery_app
        from celery.signals import task_prerun, task_postrun
        from datetime import UTC, datetime
        import threading
        # Real signal metadata only, never task args/results or model inputs.
        def execution_marker(sender=None, task_id=None, signal=None, **_kwargs):
            print(json.dumps({'source':'celery real task signal','event':signal.name,
                              'task_id':task_id,'task_name':sender.name,
                              'thread_id':threading.get_ident(),
                              'observed_utc':datetime.now(UTC).isoformat()}),flush=True)
        task_prerun.connect(execution_marker, weak=False)
        task_postrun.connect(execution_marker, weak=False)
        pool = ['--pool=solo'] if args.worker_concurrency == 1 else ['--pool=threads', '--concurrency='+str(args.worker_concurrency)]
        celery_app.worker_main(['worker', '-Q', 'property_ai.auth001', *pool, '--loglevel=info', '--hostname=auth001@%h'])
