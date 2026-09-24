"""Only directly changed intent/025/knowledge/notification boundaries, no DB fixture."""
from pathlib import Path
from uuid import uuid4
import site
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT / '.venv/Lib/site-packages'))
sys.path.insert(0, str(ROOT / 'backend'))
FILES = ['test_task_intents_026.py', 'test_database_recovery_025.py',
         'test_knowledge_management.py', 'test_sla_notification_scheduler.py']
if '--child' in sys.argv:
    import pytest
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp', str(ROOT / 'tmp' / ('rt026-unit-' + uuid4().hex))]
                                + ['backend/tests/unit/' + p for p in FILES]))
label = sys.argv[1]
target = Path(__file__).parent / ('implementation-tests-' + label + '.txt')
if target.exists():
    raise RuntimeError('Preserve existing result; label only a necessary new test run')
result = subprocess.run([sys.executable, '-I', '-X', 'utf8', __file__, '--child'], cwd=ROOT,
                        capture_output=True, text=True, encoding='utf-8')
with target.open('x', encoding='utf-8') as stream:
    stream.write(result.stdout + result.stderr)
print(result.stdout)
raise SystemExit(result.returncode)
