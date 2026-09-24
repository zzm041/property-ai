"""Only direct pure unit regressions; no integration fixture, DB or broker."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
names = ['database_recovery_025', 'celery_ai_task', 'incident_aggregation',
         'auto_dispatch', 'auto_reassign_exception', 'ai_analysis_input',
         'resident_work_order_actions', 'day2_config_enqueue',
         'day4_failed_tasks', 'maintenance_dispatch_024', 'work_order_contact_024',
         'miniprogram_work_order_lifecycle']
args = ['-q', '-p', 'no:cacheprovider'] + ['backend/tests/unit/test_'+x+'.py' for x in names]
code = ('import site,sys;site.addsitedir('+repr(str(ROOT/'.venv/Lib/site-packages'))
        +');sys.path.insert(0,'+repr(str(ROOT/'backend'))+');import pytest;sys.exit(pytest.main('+repr(args)+'))')
out_path = Path(__file__).with_name('focused-'+sys.argv[1]+'.txt')
with out_path.open('x', encoding='utf-8') as out:
    p = subprocess.Popen([sys.executable, '-I', '-c', code], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        print(line, end=''); out.write(line); out.flush()
    raise SystemExit(p.wait())
