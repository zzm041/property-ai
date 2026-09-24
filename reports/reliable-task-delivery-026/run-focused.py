"""Only new snapshot guard tests; does not run025 or business fixtures."""
from pathlib import Path
import site
import subprocess
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
sys.path.insert(0,str(ROOT/'backend'))
if len(sys.argv)>1 and sys.argv[1]=='--child':
    import pytest
    raise SystemExit(pytest.main(['-q','backend/tests/unit/test_recovery_snapshot_guard.py','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp'/('rt026-pytest-'+uuid4().hex))]))
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
label=sys.argv[1] if len(sys.argv)>1 else 'initial'
with (Path(__file__).parent/('snapshot-guard-tests-'+label+'.txt')).open('x',encoding='utf-8') as output:
    output.write(result.stdout+result.stderr)
print(result.stdout);print('exit_code='+str(result.returncode))
raise SystemExit(result.returncode)
