"""018 preflight: existing synthetic identities, SELECT-only DB, no data setup writes."""
from pathlib import Path
from datetime import UTC, datetime
import json
import runpy
import secrets
import subprocess

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / 'tmp/house-csv-roundtrip-018/context.json'
h = runpy.run_path(str(REPO / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
import httpx


def main():
    settings = h['_guard']()
    prior = json.loads((REPO / 'tmp/manager-wo-cancel-017/context.json').read_text(encoding='utf-8'))
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    ctx = json.loads(PRIVATE.read_text(encoding='utf-8')) if PRIVATE.exists() else {
        'run_id': 'csv018-' + secrets.token_hex(6), 'done': {}, 'last_sms_sent_at': {},
        **{k: prior[k] for k in ('tenant_id', 'property_id', 'phones', 'identities')},
    }
    PRIVATE.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding='utf-8')
    engine = create_engine(settings.test_database_url)
    result = {'task_id': 'HOUSE-CSV-ROUNDTRIP-018', 'run_id': ctx['run_id'], 'database': '127.0.0.1:3307/property_ai_auth001_test',
              'frontend_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
              'backend_sha': '4a8ae835388c6ee1da4d9bd088f7fc73356644e4',
              'mode': 'Backend unchanged; retained API/Worker, no restart, identity/house/business writes or fixture recreation.'}
    try:
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            result['revision'] = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert result['revision'] == '20260919_0009'
            result['property'] = dict(c.execute(text('SELECT id,tenant_id,name FROM properties WHERE id=:id'), {'id': ctx['property_id']}).mappings().one())
            assert result['property']['tenant_id'] == ctx['tenant_id']
            result['house_count_before'] = c.execute(text('SELECT count(*) FROM houses WHERE property_id=:id'), {'id': ctx['property_id']}).scalar_one()
        result['health'] = httpx.get('http://127.0.0.1:8083/api/v1/healthz', timeout=10).json()
        q = subprocess.run(['docker','exec','property-ai-rabbitmq-1','rabbitmqctl','list_queues','-p','property_ai_auth001','name','messages_ready','messages_unacknowledged','consumers'],capture_output=True,text=True,timeout=30)
        assert q.returncode == 0
        result['queue'] = {'vhost': 'property_ai_auth001', 'actual_output': q.stdout, 'message_content_read': False, 'shared_vhost_accessed': False}
        p = subprocess.run(['powershell','-NoProfile','-Command','Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in 46236,41920,46688 } | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Depth 3'],capture_output=True,text=True,timeout=20)
        assert p.returncode == 0
        result['processes'] = json.loads(p.stdout)
        assert {r['ProcessId'] for r in result['processes']} == {46236,41920,46688}
        result['observed_at_utc'] = datetime.now(UTC).isoformat()
        (OUT / 'preflight.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'run_id': ctx['run_id'], 'revision': result['revision'], 'house_count_before': result['house_count_before']}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
