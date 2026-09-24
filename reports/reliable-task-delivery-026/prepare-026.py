"""Reuse normal synthetic identities/resources; create no business orders."""
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).parent))
from common026 import *
if '--init' in sys.argv:
    assert not (PRIVATE/'context.json').exists(), 'Keep one run and all budgets'
    old=json.loads((ROOT/'tmp/db-task-recovery-025/context.json').read_text(encoding='utf-8'))
    ctx={k:old[k] for k in ('phones','identities','tenant_id','property_id','employee_id','houses')}
    ctx.update(run_id='rt026-'+uuid4().hex[:10],tokens={},orders={},steps={},processes={},kills={'api_publisher':0,'worker':0})
    save_context(ctx);(PRIVATE/'intent-ids.json').write_text('[]',encoding='utf-8')
    print(json.dumps({'run_id':ctx['run_id'],'objects_created':0}));raise SystemExit()
ctx=context();s=settings();guard(s);c=client(ctx['run_id'])
try:
    for actor in ('manager','resident','staff'):
        if actor not in ctx['tokens']:
            ctx['tokens'][actor]=c.sms_login(ctx['phones'][actor],actor)['access_token'];save_context(ctx)
        me=c.data('GET','/me',token=ctx['tokens'][actor],label='check-'+actor)
        assert me['user_id']==ctx['identities'][actor] and 'SYSTEM_ADMIN' not in me['roles']
    engine=create_database_engine(s.active_database_url)
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        daily=[dict(r) for r in conn.execute(text('SELECT business_date,current_load,on_duty FROM employee_daily_statuses WHERE employee_id=:id AND property_id=:p ORDER BY business_date'),{'id':ctx['employee_id'],'p':ctx['property_id']}).mappings()]
        active=conn.execute(text("SELECT COUNT(*) FROM work_order_assignments WHERE employee_id=:id AND status IN ('ASSIGNED','ACCEPTED','PROCESSING')"),{'id':ctx['employee_id']}).scalar_one()
    engine.dispose()
    assert active==0,'Do not alter busy/old objects'
    write('runtime-setup.json',{'run_id':ctx['run_id'],'requests':c.requests,'property_id':ctx['property_id'],'employee_id':ctx['employee_id'],'normal_auth':True,'daily':daily,'active_assignments':active,'new_orders':0,'protected_old_orders':'no operations'})
    print(json.dumps({'run_id':ctx['run_id'],'auth':'formal SMS','new_orders':0,'active_assignments':active}))
finally:c.close()
