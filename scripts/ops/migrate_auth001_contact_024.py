"""Owner-approved additive 0011 only. Default read-only; never provision/seed/drop."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts/ops'))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from auth001_isolated_runtime import isolated_database_url, load_source_settings
from app.core.database import assert_isolated_auth_database, create_database_engine

PARENT = '20260919_0009'
TARGET = '20260924_0011_contact_phone'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def snapshot(connection):
    inspector = inspect(connection)
    columns = [{key: str(column[key]) if key == 'type' else column[key]
                for key in ('name', 'type', 'nullable', 'default')}
               for column in inspector.get_columns('work_orders')]
    # Historical order12 is immutable evidence: only read/hash, never replay.
    fields = ','.join('`' + c['name'] + '`' for c in columns if c['name'] != 'contact_phone_e164')
    order12 = [dict(row) for row in connection.execute(text('SELECT ' + fields + ' FROM work_orders WHERE id=12')).mappings()]
    return {
        'database': connection.execute(text('SELECT DATABASE()')).scalar_one(),
        'revision': connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all(),
        'columns': columns,
        'table_names_hash': digest(inspector.get_table_names()),
        'work_order_count': connection.execute(text('SELECT COUNT(*) FROM work_orders')).scalar_one(),
        'order12_hash_excluding_new_column': digest(order12),
        'constraints_hash': digest([inspector.get_indexes('work_orders'), inspector.get_foreign_keys('work_orders'), inspector.get_unique_constraints('work_orders')]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = load_source_settings()
    url = isolated_database_url(source)
    settings = source.model_copy(update={'test_database_url': url})
    assert_isolated_auth_database(settings, database_url=url)
    parsed = make_url(url)
    print(json.dumps({'guard': 'PASS', 'app_env': settings.app_env,
          'target': {'host': parsed.host, 'port': parsed.port, 'database': parsed.database},
          'apply': args.apply}, ensure_ascii=False), flush=True)
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            before = snapshot(connection)
            print(json.dumps({'before': before}, default=str, ensure_ascii=False), flush=True)
            if not args.apply:
                return
            if before['revision'] != [PARENT] or any(c['name'] == 'contact_phone_e164' for c in before['columns']):
                raise RuntimeError('Refuse unexpected baseline; no automatic repair or migration replay')
            connection.rollback()
            config = Config(str(ROOT / 'backend/alembic.ini'))
            config.set_main_option('script_location', str(ROOT / 'backend/alembic'))
            config.attributes['database_url'] = url
            config.attributes['connection'] = connection
            os.environ['APP_ENV'] = 'test'
            command.upgrade(config, TARGET)
            after = snapshot(connection)
            expected = {'name': 'contact_phone_e164', 'type': 'VARCHAR(16)', 'nullable': True, 'default': None}
            assert after['revision'] == [TARGET]
            assert [c for c in after['columns'] if c['name'] != 'contact_phone_e164'] == before['columns']
            assert expected in after['columns']
            for field in ('database', 'table_names_hash', 'work_order_count', 'order12_hash_excluding_new_column', 'constraints_hash'):
                assert before[field] == after[field], field
            assert connection.execute(text('SELECT COUNT(*) FROM work_orders WHERE contact_phone_e164 IS NOT NULL')).scalar_one() == 0
            print(json.dumps({'after': after, 'existing_rows_unfilled': True, 'verification': 'PASS'}, default=str, ensure_ascii=False), flush=True)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
