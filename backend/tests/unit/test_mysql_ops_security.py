"""CLI boundary tests only; no Docker or real database operations."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def ops(monkeypatch):
    directory=ROOT/'scripts/ops'
    monkeypatch.syspath_prepend(str(directory))
    modules={}
    for name in ('mysql_backup','mysql_restore'):
        spec=importlib.util.spec_from_file_location(name,directory/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);modules[name]=module
    monkeypatch.setenv('PROPERTY_AI_MYSQL_PASSWORD','synthetic-secret-never-argv')
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs));return SimpleNamespace(returncode=0,stdout=b'SELECT 1;\n',stderr=b'')
    monkeypatch.setattr(subprocess,'run',run)
    return SimpleNamespace(backup=modules['mysql_backup'],restore=modules['mysql_restore'],calls=calls)

def arguments(tmp_path,restore=False):
    args=['--container','synthetic-mysql','--database','property_ai_restore_drill']
    if restore:
        dump=tmp_path/'input.sql';dump.write_bytes(b'SELECT 1;\n');return args+['--dump',str(dump)]
    return args+['--out',str(tmp_path/'backup.sql')]

@pytest.mark.parametrize('restore',[False,True])
def test_credentials_never_enter_process_arguments(ops,tmp_path,restore):
    command=ops.restore if restore else ops.backup
    assert command.main(arguments(tmp_path,restore))==0
    for argv,kwargs in ops.calls:
        assert not any('synthetic-secret' in value for value in argv)
        assert argv[:4]==['docker','exec','-e','MYSQL_PWD']
        assert kwargs['env']['MYSQL_PWD']=='synthetic-secret-never-argv'
        assert kwargs['timeout']==300

@pytest.mark.parametrize('database',['x`; DROP DATABASE y; --','-bad','a.b','a'*65,'mysql','SYS','information_schema','performance_schema'])
def test_invalid_restore_database_never_reaches_docker(ops,tmp_path,database):
    args=arguments(tmp_path,True);args[2:4]=['--database='+database]
    assert ops.restore.main(args+['--create-database'])==2
    assert not ops.calls

def test_development_target_is_case_insensitive_guard(ops,tmp_path):
    args=arguments(tmp_path,True);args[3]='PROPERTY_AI'
    assert ops.restore.main(args)==2 and not ops.calls

@pytest.mark.parametrize('restore',[False,True])
def test_missing_secret_fails_before_external_operation(ops,tmp_path,monkeypatch,restore):
    monkeypatch.delenv('PROPERTY_AI_MYSQL_PASSWORD')
    module=ops.restore if restore else ops.backup
    assert module.main(arguments(tmp_path,restore))==2 and not ops.calls

@pytest.mark.parametrize('restore',[False,True])
def test_old_password_flag_is_rejected_without_echoing_value(ops,tmp_path,capsys,restore):
    module=ops.restore if restore else ops.backup
    assert module.main(arguments(tmp_path,restore)+['--password','synthetic-secret-never-argv'])==2
    assert not ops.calls
    captured=capsys.readouterr();assert 'synthetic-secret' not in captured.out+captured.err

def test_existing_backup_is_never_overwritten_or_dumped(ops,tmp_path):
    args=arguments(tmp_path);target=tmp_path/'backup.sql';target.write_bytes(b'KEEP')
    assert ops.backup.main(args)==2
    assert target.read_bytes()==b'KEEP' and not ops.calls

@pytest.mark.parametrize('restore',[False,True])
def test_external_stderr_is_not_printed(ops,tmp_path,monkeypatch,capsys,restore):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b'',stderr=b'synthetic-secret-never-argv'))
    module=ops.restore if restore else ops.backup
    assert module.main(arguments(tmp_path,restore))==1
    captured=capsys.readouterr();assert 'synthetic-secret' not in captured.out+captured.err
    assert not (tmp_path/'backup.sql').exists()

def test_restore_create_database_uses_validated_identifier_and_separate_credentials(ops,tmp_path):
    assert ops.restore.main(arguments(tmp_path,True)+['--create-database'])==0
    assert len(ops.calls)==2
    assert 'CREATE DATABASE IF NOT EXISTS `property_ai_restore_drill`' in ops.calls[0][0][-1]
    assert ops.calls[1][1]['input']==b'SELECT 1;\n'

def test_backup_preserves_raw_dump_bytes(ops,tmp_path):
    assert ops.backup.main(arguments(tmp_path))==0
    assert (tmp_path/'backup.sql').read_bytes()==b'SELECT 1;\n'

@pytest.mark.parametrize('restore',[False,True])
def test_timeout_is_reported_without_retry_or_sensitive_exception(ops,tmp_path,monkeypatch,capsys,restore):
    calls=[]
    def timeout(*a,**k):
        calls.append(a);raise subprocess.TimeoutExpired('synthetic-secret-never-argv',300)
    monkeypatch.setattr(subprocess,'run',timeout)
    module=ops.restore if restore else ops.backup
    assert module.main(arguments(tmp_path,restore))==1 and len(calls)==1
    captured=capsys.readouterr();assert 'synthetic-secret' not in captured.out+captured.err
    if restore:assert 'partially changed' in captured.out

@pytest.mark.parametrize('option,value',[('--container','--host=foreign'),('--user','root;cmd'),('--password-env','PASSWORD=secret')])
def test_invalid_operator_inputs_fail_before_docker(ops,tmp_path,option,value):
    args=arguments(tmp_path)
    if option=='--container':args[:2]=[option+'='+value]
    else:args.append(option+'='+value)
    assert ops.backup.main(args)==2 and not ops.calls

def test_create_failure_never_starts_restore(ops,tmp_path,monkeypatch):
    called=[]
    def fail(*a,**k):called.append(a);return SimpleNamespace(returncode=1,stdout=b'',stderr=b'')
    monkeypatch.setattr(subprocess,'run',fail)
    assert ops.restore.main(arguments(tmp_path,True)+['--create-database'])==1 and len(called)==1

def test_output_created_during_dump_is_not_overwritten(ops,tmp_path,monkeypatch):
    target=tmp_path/'backup.sql'
    def race(*a,**k):target.write_bytes(b'KEEP');return SimpleNamespace(returncode=0,stdout=b'REPLACE',stderr=b'')
    monkeypatch.setattr(subprocess,'run',race)
    assert ops.backup.main(arguments(tmp_path))==1
    assert target.read_bytes()==b'KEEP'
