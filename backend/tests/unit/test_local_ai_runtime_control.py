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


def test_termination_rejects_listener_owned_by_another_process(monkeypatch):
    receipt={'pid':12,'executable':'C:/fixed/python.exe','launched_at_utc':'2026-09-29T00:00:00+00:00'}
    monkeypatch.setattr(m,'bound_runtime_identity',lambda value:{'pid':12})
    monkeypatch.setattr(m,'loopback_listener_pid',lambda port:13)
    with pytest.raises(RuntimeError,match='LISTENER_IDENTITY_CHANGED'):
        m.terminate_bound_runtime(receipt)


def test_nonzero_taskkill_is_accepted_only_after_independent_exit_confirmation(monkeypatch):
    receipt={'pid':12,'executable':'C:/fixed/python.exe','launched_at_utc':'2026-09-29T00:00:00+00:00'}
    identities=iter([{'pid':12},RuntimeError('gone')])
    def identity(value):
        result=next(identities)
        if isinstance(result,Exception):raise result
        return result
    monkeypatch.setattr(m,'bound_runtime_identity',identity)
    monkeypatch.setattr(m,'loopback_listener_pid',lambda port:12)
    monkeypatch.setattr(m,'loopback_listener_closed',lambda port:True)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=128))
    result=m.terminate_bound_runtime(receipt)
    assert result['taskkill_exit_race'] is True
    assert result['stopped']=={
        'pid':12,'tree_exit_confirmed':True,'listener_exit_confirmed':True,
    }
