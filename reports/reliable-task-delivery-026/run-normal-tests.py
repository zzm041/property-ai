"""Scoped new supervision tests only; no normal business or prior82 rerun."""
from pathlib import Path
import site
import subprocess
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
if '--child' in sys.argv:
    site.addsitedir(str(ROOT/'.venv/Lib/site-packages'))
    sys.path.insert(0,str(ROOT/'backend'))
    import pytest
    selection=['-k','snapshot_hash'] if '--snapshot' in sys.argv else []
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(ROOT/'tmp'/('normal026-unit-'+uuid4().hex)), 'backend/tests/unit/test_publisher_supervision_026.py',*selection]))
selection=['--snapshot'] if '--snapshot' in sys.argv else []
result=subprocess.run([sys.executable,'-I','-X','utf8',__file__,'--child',*selection],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
name='snapshot-tests.txt' if selection else 'supervision-tests.txt'
with (Path(__file__).parent/'normal-integration'/name).open('x',encoding='utf-8') as f:f.write(result.stdout+result.stderr)
print(result.stdout);raise SystemExit(result.returncode)
