"""Read-only process/queue closeout; no test, business, model or DB operation."""
import sys
from pathlib import Path
import importlib.util
import subprocess
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
spec=importlib.util.spec_from_file_location('env026',OUT/'environment-026.py')
env=importlib.util.module_from_spec(spec);spec.loader.exec_module(env)
ctx=context();assert 'drill_finished' in ctx
actual=env.processes()
queue=subprocess.check_output(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],text=True).strip()
write('closeout.json',{'observed_utc':stamp(),'actual_processes':actual,'queue_metadata':queue,
    'publisher_still_running':any(' publisher ' in p['CommandLine'] for p in actual),
    'current_source_hash':source_hash(),'loaded_runtime_source_hash':'57d5f9f8ffbd65b0f571d75ee4fbcea34a7af02cefe82c500d97bc5c68428aa6',
    'runtime_boundary':'API/Worker still dedicated test chain, no final takeover/commitackfix load, no claim of defaultparameter/candidate rollout',
    'closed_run':ctx['run_id'],'new_recovery_intent_list':json.loads((PRIVATE/'intent-ids.json').read_text(encoding='utf-8')),
    'quota':'unobservable; owner53percent baseline only','deadline':'2026-09-27T14:00:00+08:00'})
print(json.dumps({'pids':[p['ProcessId'] for p in actual],'publisher_still_running':any(' publisher ' in p['CommandLine'] for p in actual),'queue_metadata':queue}))
