"""Finite local candidate supervision; use Compose restart for approved deployment.

No task/domain logic, global scheduler, service installation or per-order launch.
Successful bounded child exits are restarted; three consecutive errors stop.
"""
import json
import subprocess
import time


def supervise(command, *, cycles, cwd, stop_file=None, run=subprocess.run, sleep=time.sleep):
    if not 1 <= cycles <= 24:
        raise ValueError('Supervisor needs a finite 1..24 cycle budget')
    failures = 0
    for cycle in range(cycles):
        if stop_file is not None and stop_file.exists():
            return 'STOPPED'
        result = run(command, cwd=cwd, check=False)
        failures = 0 if result.returncode == 0 else failures + 1
        print(json.dumps({'source':'local-publisher-supervisor','cycle':cycle+1,'exit_code':result.returncode,'consecutive_failures':failures}),flush=True)
        if failures >= 3:
            raise RuntimeError('Publisher supervisor stopped after three consecutive failures')
        if cycle+1 < cycles:
            sleep(2 ** failures)
    return 'BUDGET_COMPLETE'
