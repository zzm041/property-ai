"""Same approved AUTH001 boundary, real loopback Qwen, no migrations or fixtures."""
from pathlib import Path
import argparse
import json
import os
import site
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(r"D:\property-ai")
site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts/ops")]

from app.core.config import Settings
from app.core.database import assert_isolated_auth_database
from auth001_isolated_runtime import build_isolated_settings, apply_isolated_environ, describe_isolated_target
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component', choices=('api', 'worker'))
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    original = build_isolated_settings(enable_enqueue=True)
    values = original.model_dump()
    overrides = {}
    for name in ('llm', 'vlm'):
        overrides.update({f'{name}_provider': 'production', f'{name}_base_url': 'http://127.0.0.1:11435/v1',
                          f'{name}_model': 'qwen3.5:2b', f'{name}_api_key': 'local-loopback-no-auth',
                          f'{name}_timeout_seconds': 60, f'{name}_disable_thinking': True,
                          f'{name}_max_output_tokens': 512 if name == 'llm' else 256})
    settings = Settings.model_validate({**values, **overrides})
    db = make_url(settings.test_database_url)
    assert (db.host, db.port, db.database, settings.app_env) == ('127.0.0.1', 3307, 'property_ai_auth001_test', 'test')
    assert_isolated_auth_database(settings, database_url=settings.test_database_url)
    assert urlsplit(settings.celery_broker_url).path == '/property_ai_auth001'
    assert settings.celery_task_queue == 'property_ai.auth001'
    assert settings.embedding_provider == settings.reranker_provider == settings.asr_provider == 'development'
    assert settings.dev_auth_enabled is False
    assert settings.embedding_dimensions == 8 and settings.milvus_collection == 'knowledge_chunks_auth001'
    apply_isolated_environ(settings)
    for name, value in overrides.items():
        os.environ[name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
    from app.provider.factory import build_llm_provider, build_vlm_provider
    assert build_llm_provider(settings).model == build_vlm_provider(settings).model == 'qwen3.5:2b'
    sha = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    public = {'task_id': 'SELFHOST-AI-LOCAL-001', 'component': args.component,
              'python': sys.executable, 'module_root': str(ROOT / 'backend/app'), 'code_sha': sha,
              'target': describe_isolated_target(settings), 'prompt_version': WORK_ORDER_ANALYSIS_PROMPT_VERSION,
              'llm_vlm': 'real qwen3.5:2b @ loopback11435; not paid/cloud', 'thinking': False,
              'max_tokens': {'llm': 512, 'vlm': 256}, 'timeout_seconds': 60,
              'other_providers': 'Development unchanged', 'check_only': args.check_only}
    print(json.dumps(public), flush=True)
    if args.check_only:
        return
    os.chdir(ROOT / 'backend')
    if args.component == 'api':
        # Apply broker/model environment before routers import process-global Celery.
        import uvicorn
        from app.main import create_app
        uvicorn.run(create_app(settings), host='127.0.0.1', port=8083, log_level='warning')
    else:
        # Recovered interpreter, explicit existing dependencies; no global packages.
        from app.worker.celery_app import celery_app
        celery_app.worker_main(['worker', '-Q', 'property_ai.auth001', '--pool=solo', '--loglevel=info', '--hostname=auth001@%h'])


if __name__ == '__main__':
    main()
