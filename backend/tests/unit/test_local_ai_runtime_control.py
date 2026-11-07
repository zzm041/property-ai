"""No-model tests for receipt-bound local AI runtime termination."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location(
    'local_ai_runtime_control',ROOT/'scripts/ops/local_ai_runtime_control.py'
)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_windows_identity_commands_decode_with_the_oem_code_page(monkeypatch):
    calls=[]
    def check_output(*args,**kwargs):
        calls.append(kwargs)
        return '{"pid":12,"path":"C:/fixed/python.exe","started_utc":"2026-09-29T00:00:00+00:00"}'
    monkeypatch.setattr(m.subprocess,'check_output',check_output)
    assert m._process_identity(12)['pid']==12
    assert calls==[{'text':True,'encoding':'oem','stderr':m.subprocess.DEVNULL}]


def test_termination_rejects_listener_owned_by_another_process(monkeypatch):
    receipt={'pid':12,'executable':'C:/fixed/python.exe','launched_at_utc':'2026-09-29T00:00:00+00:00'}
    monkeypatch.setattr(m,'bound_runtime_identity',lambda value:{'pid':12})
    monkeypatch.setattr(m,'loopback_listener_pid',lambda port:13)
    with pytest.raises(RuntimeError,match='LISTENER_IDENTITY_CHANGED'):
        m.terminate_bound_runtime(receipt)


def test_nonzero_taskkill_fails_closed_even_when_root_already_disappeared(monkeypatch):
    receipt={'pid':12,'executable':'C:/fixed/python.exe','launched_at_utc':'2026-09-29T00:00:00+00:00'}
    tree=[{'pid':12,'path':'C:/fixed/python.exe','started_utc':'2026-09-29T00:00:00+00:00'},
        {'pid':13,'path':'C:/fixed/runner.exe','started_utc':'2026-09-29T00:00:01+00:00'}]
    monkeypatch.setattr(m,'bound_runtime_identity',lambda value:tree[0])
    monkeypatch.setattr(m,'bound_process_tree',lambda value:tree)
    monkeypatch.setattr(m,'loopback_listener_pid',lambda port:12)
    monkeypatch.setattr(m,'loopback_listener_closed',lambda port:True)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=128))
    with pytest.raises(RuntimeError,match='TREE_TERMINATION_FAILED'):
        m.terminate_bound_runtime(receipt)


def test_surviving_captured_descendant_fails_closed(monkeypatch):
    receipt={'pid':12,'executable':'C:/fixed/python.exe','launched_at_utc':'2026-09-29T00:00:00+00:00'}
    tree=[{'pid':12},{'pid':13}]
    monkeypatch.setattr(m,'bound_runtime_identity',lambda value:tree[0])
    monkeypatch.setattr(m,'bound_process_tree',lambda value:tree)
    monkeypatch.setattr(m,'loopback_listener_pid',lambda port:12)
    monkeypatch.setattr(m,'loopback_listener_closed',lambda port:True)
    monkeypatch.setattr(m,'_same_process_is_alive',lambda identity:identity['pid']==13)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    clock=[0]
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(m.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    with pytest.raises(RuntimeError,match='EXIT_UNCONFIRMED'):
        m.terminate_bound_runtime(receipt)
