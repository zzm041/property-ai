"""Bounded process-only AUTH001 fault setting; reuse the existing recovered launcher."""
from pathlib import Path
import argparse
import json
import os
import runpy
import site
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT / '.venv/Lib/site-packages'))
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts/ops'))

parser = argparse.ArgumentParser()
parser.add_argument('component', choices=('api', 'worker'))
parser.add_argument('behavior', choices=('success', 'unavailable'))
parser.add_argument('--check-only', action='store_true')
args = parser.parse_args()
os.environ['LLM_PROVIDER_BEHAVIOR'] = args.behavior
from auth001_isolated_runtime import build_isolated_settings, describe_isolated_target
from sqlalchemy.engine import make_url
from app.provider.factory import build_llm_provider
from app.provider.errors import ProviderUnavailableError

settings = build_isolated_settings()
db = make_url(settings.test_database_url)
assert (db.host, db.port, db.database, settings.app_env) == ('127.0.0.1', 3307, 'property_ai_auth001_test', 'test')
assert urlsplit(settings.celery_broker_url).path.lstrip('/') == 'property_ai_auth001'
assert settings.celery_task_queue == 'property_ai.auth001'
assert all(getattr(settings, name) == 'development' for name in ('llm_provider', 'vlm_provider', 'asr_provider', 'notification_provider'))
assert settings.llm_provider_behavior == args.behavior
assert settings.dev_auth_enabled is False
print(json.dumps({'task_id': 'AI-FALLBACK-RUNTIME-022', 'component': args.component, 'target': describe_isolated_target(settings), 'llm_provider': settings.llm_provider, 'llm_behavior': settings.llm_provider_behavior, 'python': sys.executable, 'project_modules': str(ROOT / 'backend/app'), 'check_only': args.check_only}), flush=True)
if args.check_only:
    provider = build_llm_provider(settings)
    if args.behavior == 'unavailable':
        try:
            provider.structured_completion(prompt='synthetic launcher probe', prompt_version='wo-analysis-v2')
        except ProviderUnavailableError:
            print('CHECK_ONLY: isolated guards and existing Development unavailable behavior verified; no process/DB/queue action', flush=True)
        else:
            raise AssertionError('Unavailable mode did not fail')
    else:
        assert provider.structured_completion(prompt='synthetic launcher probe', prompt_version='wo-analysis-v2')
        print('CHECK_ONLY: isolated guards and existing Development success behavior verified; no process/DB/queue action', flush=True)
else:
    launcher = ROOT / 'tmp/run_auth001_recovered.py'
    assert launcher.is_file()
    sys.argv = [str(launcher), args.component]
    runpy.run_path(str(launcher), run_name='__main__')
