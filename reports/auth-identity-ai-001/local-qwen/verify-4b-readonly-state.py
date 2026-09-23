"""Post-diagnostic SELECT evidence, no model inference or business replay."""
from datetime import UTC, datetime
from pathlib import Path
import json
import runpy

ROOT = Path(r'D:\property-ai')
OUT = Path(__file__).parent
destination = OUT / 'post-4b-readonly-state.json'
assert not destination.exists()
prior = json.loads((OUT / 'business-database-runtime.json').read_text(encoding='utf-8'))
h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
settings = h['_guard']()
engine = create_engine(settings.test_database_url)
e = {'task_id': 'SELFHOST-AI-LOCAL-001', 'observed_at_utc': datetime.now(UTC).isoformat(),
     'database': '127.0.0.1:3307/property_ai_auth001_test', 'mode': 'SELECT only; no queue connection or model generation'}
try:
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        e['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert e['revision'] == '20260919_0009'
        old = prior['work_order']
        params = {'id': old['id'], 'tenant': old['tenant_id'], 'property': old['property_id']}
        e['work_order'] = dict(c.execute(text('SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id FROM work_orders WHERE id=:id AND tenant_id=:tenant AND property_id=:property'), params).mappings().one())
        assert all(old[key] == value for key, value in e['work_order'].items())
        tables = {'analyses': 'work_order_analyses', 'ai_calls': 'ai_call_logs', 'assignments': 'work_order_assignments', 'recommendations': 'dispatch_recommendations', 'history': 'work_order_logs'}
        e['same_business_row_ids'] = {}
        for key, table in tables.items():
            ids = list(c.execute(text(f'SELECT id FROM {table} WHERE work_order_id=:id ORDER BY id'), params).scalars())
            assert ids == [row['id'] for row in prior[key]], f'{key} changed; do not claim no change'
            e['same_business_row_ids'][key] = ids
        status = dict(c.execute(text('SELECT id,status,summary,input_hash FROM work_order_analyses WHERE work_order_id=:id'), params).mappings().one())
        assert all(prior['analyses'][0][key] == value for key, value in status.items())
        e['persisted_analysis'] = status
        e['prior_orders'] = [dict(row) for row in c.execute(text('SELECT id,status,version FROM work_orders WHERE tenant_id=:tenant AND property_id=:property AND id<>:id ORDER BY id'), params).mappings()]
        assert e['prior_orders'] == prior['prior_orders']
    api = runpy.run_path(str(OUT / 'probe-model.py'))['api']
    e['loaded_local_models_after_keepalive'] = api('/api/ps')['models']
    e['runtime_startup_headers'] = {name: json.loads((ROOT / f'tmp/selfhost-ai-local/{name}.stdout.log').read_text(encoding='utf-8').splitlines()[0]) for name in ('api', 'worker')}
    e['result'] = 'UNCHANGED_PERSISTED_ORDER_AND_HISTORY_NO_4B_BUSINESS_RESULT_WRITTEN'
    destination.write_text(json.dumps(e, ensure_ascii=False, indent=2, default=str)+'\n', encoding='utf-8')
    print(json.dumps({'result': e['result'], 'work_order': e['work_order'], 'loaded_models': e['loaded_local_models_after_keepalive']}, ensure_ascii=False))
finally:
    engine.dispose()
