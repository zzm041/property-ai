"""Read current AUTH001 synthetic prerequisites; no fixture/business writes."""
from pathlib import Path
import json
import runpy
import secrets

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = ROOT / 'tmp/ai-analysis-input-019/context.json'
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text


def main():
    settings = h['_guard']()
    prior = json.loads((ROOT / 'tmp/manager-wo-cancel-017/context.json').read_text(encoding='utf-8'))
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
        'run_id':'ai019-'+secrets.token_hex(6), 'done':{}, 'last_sms_sent_at':{},
        **{k:prior[k] for k in ('phones','identities','employees','tenant_id','property_id','house_id')},
    }
    PRIVATE.write_text(json.dumps(ctx,ensure_ascii=False,indent=2),encoding='utf-8')
    engine = create_engine(settings.test_database_url)
    result = {'task_id':'AI-ANALYSIS-INPUT-019','run_id':ctx['run_id'],'product_sha':'d1c4e9f6891a47e052e4a00b111e44a335c4fa39','mode':'SELECT-only reuse of current synthetic identity/relation/staff data; no old flows replayed'}
    try:
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            result['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert result['revision'] == '20260919_0009'
            result['house'] = dict(c.execute(text('SELECT id,tenant_id,property_id,status FROM houses WHERE id=:id'), {'id':ctx['house_id']}).mappings().one())
            assert result['house']['tenant_id'] == ctx['tenant_id'] and result['house']['property_id'] == ctx['property_id'] and result['house']['status'] == 'ACTIVE'
            result['relation'] = [dict(r) for r in c.execute(text("SELECT id,user_id,house_id,relation_type,status FROM resident_house_relations WHERE house_id=:house AND user_id=:user AND status='ACTIVE'"), {'house':ctx['house_id'],'user':ctx['identities']['resident']}).mappings()]
            assert result['relation']
            result['staff'] = [dict(r) for r in c.execute(text('SELECT employee_id,on_duty,current_load,business_date FROM employee_daily_statuses WHERE property_id=:property'), {'property':ctx['property_id']}).mappings()]
            assert all(r['on_duty']=='ON_DUTY' and r['current_load']==0 for r in result['staff'])
        result['helper_note'] = 'Initial SELECT referenced max_capacity on the daily-status table and failed before any business request. Removed that incorrect projection; no schema/data modification or business replay.'
        (OUT/'fixture-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({'run_id':ctx['run_id'],'revision':result['revision'],'identities':ctx['identities']}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
