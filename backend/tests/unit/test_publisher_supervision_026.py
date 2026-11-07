"""Only newly added local supervisor limits; no DB or original82 rerun."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[3] / 'scripts/ops/supervise_task_publisher.py'
spec = importlib.util.spec_from_file_location('publisher_supervision', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_normal_finite_exit_restarts_without_per_order_action():
    run = Mock(return_value=SimpleNamespace(returncode=0))
    assert module.supervise(['publisher'], cycles=3, cwd=path.parent, run=run, sleep=Mock()) == 'BUDGET_COMPLETE'
    assert run.call_count == 3


def test_three_consecutive_failures_stop_and_do_not_loop():
    run = Mock(return_value=SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='three consecutive'):
        module.supervise(['publisher'], cycles=24, cwd=path.parent, run=run, sleep=Mock())
    assert run.call_count == 3


def test_success_resets_only_supervisor_error_count_not_task_budget():
    run = Mock(side_effect=[SimpleNamespace(returncode=i) for i in (1, 1, 0, 1)])
    assert module.supervise(['publisher'], cycles=4, cwd=path.parent, run=run, sleep=Mock()) == 'BUDGET_COMPLETE'


def test_stop_file_prevents_new_child(tmp_path):
    run = Mock()
    assert module.supervise(['publisher'], cycles=1, cwd=path.parent, stop_file=tmp_path, run=run) == 'STOPPED'
    run.assert_not_called()


@pytest.mark.parametrize('cycles', [0, 25])
def test_finite_supervision_budget_required(cycles):
    with pytest.raises(ValueError): module.supervise(['publisher'], cycles=cycles, cwd=path.parent)


@pytest.mark.parametrize('valid', [False, True])
def test_snapshot_hash_checked_before_any_service_access(valid, monkeypatch):
    import runpy
    namespace = runpy.run_path(str(path.parents[2] / 'reports/reliable-task-delivery-026/normal-integration.py'))
    serve = namespace['serve']
    state = serve.__globals__
    expected = 'fe9fcdd742ffe6331fb440f94019a067a9325aaa9a9b91c2d8a009ea615c8926'
    monkeypatch.setitem(state, 'source_hash', lambda: expected if valid else 'changed')
    configured = Mock(side_effect=RuntimeError('stop before any real service connection'))
    monkeypatch.setitem(state, 'configured', configured)
    with pytest.raises(RuntimeError if valid else AssertionError):
        serve('publisher')
    assert configured.call_count == int(valid)
