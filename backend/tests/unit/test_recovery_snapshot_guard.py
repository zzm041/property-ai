"""026 new backup guards only: no database, Docker or old025 regression."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SOURCE=Path(__file__).resolve().parents[3]/'scripts/ops/auth001_recovery_snapshot.py'
spec=importlib.util.spec_from_file_location('snapshot026',SOURCE)
snapshot=importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def settings(database='property_ai_auth001_test',host='127.0.0.1',port=3307,env='test'):
    return SimpleNamespace(app_env=env,active_database_url=f'mysql+pymysql://test:synthetic@{host}:{port}/{database}')


@pytest.mark.parametrize('changes',[
    {'database':'property_ai_test'},{'database':'property_ai'},{'database':'property_ai_day4'},
    {'database':'property_ai_staging'},{'host':'localhost'},{'host':'example.test'},
    {'port':3306},{'env':'production'},{'env':'development'},
])
def test_wrong_target_rejected_before_io(monkeypatch,changes):
    engine=MagicMock();monkeypatch.setattr(snapshot,'create_database_engine',engine)
    with pytest.raises(ValueError):snapshot.take_snapshot(settings(**changes),'rt026-12345678')
    engine.assert_not_called()


def test_revision_and_driver_not_silently_repaired():
    snapshot.validate_target(settings(),snapshot.REVISION)
    with pytest.raises(ValueError):snapshot.validate_target(settings(),'20260919_0009')
    s=settings();s.active_database_url=s.active_database_url.replace('mysql+pymysql','mysql')
    with pytest.raises(ValueError):snapshot.validate_target(s,snapshot.REVISION)


@pytest.mark.parametrize("run_id", ["../backup", "rt026-../12345678", "rt026-ABCD1234", "rt026-a"])
def test_no_path_escape_or_implicit_run_marker(tmp_path,run_id):
    with pytest.raises(ValueError):snapshot.destination(run_id,root=tmp_path)


def test_existing_backup_is_never_overwritten(tmp_path):
    p=snapshot.destination('rt026-12345678',root=tmp_path);p.mkdir()
    with pytest.raises(ValueError):snapshot.destination('rt026-12345678',root=tmp_path)


def test_dump_command_scopes_one_database_and_no_password_value():
    command=snapshot.dump_command();sql_command=command[-1]
    assert command[:3]==['docker','exec','property-ai-mysql-test-1']
    assert sql_command.endswith('--databases property_ai_auth001_test')
    assert '--single-transaction' in sql_command and '--skip-lock-tables' in sql_command
    assert '--skip-add-drop-table' in sql_command and '--no-create-db' in sql_command
    assert 'synthetic' not in sql_command and '-p' not in sql_command.replace('--set-gtid-purged=OFF','')
    assert '$MYSQL_ROOT_PASSWORD' in sql_command and 'DROP DATABASE' not in sql_command


def test_container_metadata_preserves_existing_wildcard_risk(monkeypatch):
    import json
    result=MagicMock(stdout=json.dumps({'running':True,'ports':{'3306/tcp':[{'HostIp':'0.0.0.0','HostPort':'3307'}]}}))
    monkeypatch.setattr(snapshot.subprocess,'run',lambda *a,**kw:result)
    assert snapshot.inspect_container()['existing_wildcard_publish'] is True
    result.stdout=json.dumps({'running':True,'ports':{'3306/tcp':[{'HostIp':'127.0.0.1','HostPort':'3306'}]}})
    with pytest.raises(ValueError):snapshot.inspect_container()
