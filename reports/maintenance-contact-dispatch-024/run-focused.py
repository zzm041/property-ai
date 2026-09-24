"""Only direct unit regressions, no integration fixtures or environment writes."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
tests = [
    'maintenance_dispatch_024','work_order_contact_024','employee_scope_024',
    'employee_capability_update','employee_management_view','employee_authorization',
    'employee_policy','official_auth_security','rbac_mutation_service','rbac_mutation_repository',
    'ai_analysis_input','ai_policy','auto_dispatch','auto_reassign_exception',
    'sla_notification_scheduler','work_order_authorization','work_order_policy',
    'work_order_receipt_media','work_order_reopen_history','resident_work_order_actions',
    'manager_work_order_cancel','notification_events_inbox','miniprogram_work_order_lifecycle',
]
args = ['-q','-p','no:cacheprovider'] + ['backend/tests/unit/test_'+x+'.py' for x in tests]
code = 'import site,sys;site.addsitedir('+repr(str(ROOT/'.venv/Lib/site-packages'))+');sys.path.insert(0,'+repr(str(ROOT/'backend'))+');import pytest;sys.exit(pytest.main('+repr(args)+'))'
with Path(__file__).with_name('focused-final.txt').open('w',encoding='utf-8') as out:
    out.write('024 scoped unit only; no database fixtures; actual subprocess output\n')
    proc = subprocess.Popen([sys.executable,'-I','-c',code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    for line in proc.stdout:
        print(line,end='');out.write(line);out.flush()
    raise SystemExit(proc.wait())
