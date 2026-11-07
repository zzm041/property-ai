"""Read-only AUTH001 snapshot before an explicitly approved schema change.

No restore, migration, container start, message operation or overwrite exists.
The private SQL stays on host D: (not the container tmpfs) and must not enter Git.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.database import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = ROOT / 'tmp/reliable-task-delivery-026/backups'
CONTAINER = 'property-ai-mysql-test-1'
DATABASE = 'property_ai_auth001_test'
REVISION = '20260924_0011_contact_phone'


def validate_target(settings, revision: str) -> None:
    url = make_url(settings.active_database_url)
    if (settings.app_env != 'test' or url.drivername != 'mysql+pymysql'
            or url.host != '127.0.0.1' or url.port != 3307
            or url.database != DATABASE or revision != REVISION):
        raise ValueError('Snapshot requires exact AUTH001 test target at approved0011')


def destination(run_id: str, *, root: Path = BACKUP_ROOT) -> Path:
    if not re.fullmatch(r'rt026-[a-z0-9]{8,24}', run_id):
        raise ValueError('A new rt026 run marker is required')
    resolved_root = root.resolve()
    result = (root / run_id).resolve()
    if result.parent != resolved_root or result.exists():
        raise ValueError('Snapshot path must be new and within the named backup root')
    return result


def dump_command() -> list[str]:
    # Password is expanded only inside the existing container, never put in
    # process arguments, host logs or a newly created option/config file.
    return ['docker', 'exec', CONTAINER, 'sh', '-c',
            'test -n "$MYSQL_ROOT_PASSWORD" || exit 21; '
            'export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysqldump '
            '--single-transaction --skip-lock-tables --no-tablespaces '
            '--set-gtid-purged=OFF --skip-add-drop-table --no-create-db '
            '--routines --triggers --hex-blob --default-character-set=utf8mb4 '
            '--user=root --databases property_ai_auth001_test']


def inspect_container() -> dict:
    # Never inspect Config.Env (credentials); selected metadata only.
    template = '{"id":{{json .Id}},"image":{{json .Config.Image}},"running":{{json .State.Running}},"ports":{{json .NetworkSettings.Ports}},"mounts":{{json .Mounts}},"tmpfs":{{json .HostConfig.Tmpfs}}}'
    result = subprocess.run(['docker','inspect','--format',template,CONTAINER],
                            capture_output=True,text=True,check=True,timeout=20)
    info = json.loads(result.stdout)
    ports = info['ports'].get('3306/tcp') or []
    # Existing wildcard publishing includes loopback; it is a recorded release
    # risk, not permission to change bindings or connect through another host.
    if not info['running'] or not any(p.get('HostIp') in {'127.0.0.1','0.0.0.0'} and p.get('HostPort')=='3307' for p in ports):
        raise ValueError('Container is not the running3307 target reachable via loopback')
    info['existing_wildcard_publish']=any(p.get('HostIp') in {'0.0.0.0','::'} for p in ports)
    return info


def take_snapshot(settings, run_id: str) -> dict:
    # Guard before opening a connection or creating any file.
    validate_target(settings, REVISION)
    target = destination(run_id)
    engine = create_database_engine(settings.active_database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            validate_target(settings, revision)
            engines = connection.execute(text('SELECT DISTINCT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=:db AND TABLE_TYPE=\'BASE TABLE\''), {'db':DATABASE}).scalars().all()
            if not engines or set(engines) != {'InnoDB'}:
                raise ValueError('Single-transaction backup requires all target base tables InnoDB')
    finally:
        engine.dispose()
    info = inspect_container()
    target.mkdir(parents=True, exist_ok=False)
    output = target / 'auth001-0011.sql.incomplete'
    try:
        with output.open('xb') as stream:
            result = subprocess.run(dump_command(),stdout=stream,stderr=subprocess.PIPE,check=False,timeout=120)
        if result.returncode != 0:
            # Keep partial bytes privately; do not expose raw database errors.
            raise RuntimeError(f'Snapshot failed, exit={result.returncode}; private partial retained')
        raw = output.read_bytes()
        if len(raw) < 100 or b'CREATE TABLE' not in raw or b'Dump completed' not in raw:
            raise RuntimeError('Incomplete dump; private partial retained')
        completed = target / 'auth001-0011.sql'
        output.rename(completed)  # new exact destination, no overwrite
        return {'task_id':'RELIABLE-TASK-DELIVERY-026','run_id':run_id,
                'observed_utc':datetime.now(UTC).isoformat(),'database':DATABASE,
                'revision':revision,'container':info,'private_host_path':str(completed),
                'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                'mode':'readonly logical snapshot; no restore validation yet',
                'retention':'host D: retained; ignored private asset, no cleanup or Git submission'}
    except subprocess.TimeoutExpired:
        # subprocess timeout terminates only this dedicated docker client,
        # never the DB/container; retain partial and fail, no automatic retry.
        raise RuntimeError('Snapshot timed out; private partial retained') from None


def main():
    from auth001_isolated_runtime import build_isolated_settings
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    try:
        result=take_snapshot(build_isolated_settings(enable_enqueue=False),args.run_id)
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error_type':type(exc).__name__,
                          'detail':'No restore/migration/service changes; inspect private partial locally'}))
        return 1
    report=ROOT/'reports/reliable-task-delivery-026/snapshot.json'
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
