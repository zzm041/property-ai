"""PA-131: a failed request must not stop the shared Qwen runtime.

The adapter used to call terminate_bound_runtime() for every qwen failure
reason. One RESOURCE_FLOOR therefore killed the whole 11435 tree while the
binding kept the dead pid, and the next request failed at RUNTIME_IDENTITY
(INTERNAL_PRE_RESERVATION_ERROR) until someone ran down+up by hand.
"""

import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('local_ai_http_131', ROOT / 'scripts/ops/local_ai_http.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def test_floor_and_timeouts_are_not_runtime_stop_reasons() -> None:
    for reason in (
        'RESOURCE_FLOOR',
        'RESOURCE_OBSERVATION_FAILED',
        'INFERENCE_TIMEOUT',
        'LOAD_TIMEOUT',
        'R2_WINDOW_EXHAUSTED',
        'REMOTE_RESPONSE_INVALID',
    ):
        assert adapter.runtime_stop_required(reason) is False, reason
        assert reason in adapter.RUNTIME_LEFT_RUNNING_REASONS, reason


def test_only_dirty_runtime_reasons_may_stop_the_shared_runtime() -> None:
    assert adapter.RUNTIME_STOP_REASONS == frozenset(
        {'REMOTE_CLIENT_FAILED', 'DESCENDANTS_REMAINED_AFTER_LAUNCHER_EXIT'}
    )
    assert adapter.runtime_stop_required('REMOTE_CLIENT_FAILED') is True
    assert adapter.runtime_stop_required('DESCENDANTS_REMAINED_AFTER_LAUNCHER_EXIT') is True


def test_floor_failure_counts_as_complete_so_no_shared_barrier_is_written() -> None:
    left_running, unknown = adapter.request_completion_state(
        kind='qwen',
        reason='RESOURCE_FLOOR',
        success=False,
        owned_exit_confirmed=True,
        controlled_remote_stop=False,
    )

    assert left_running is True
    assert unknown is False, 'refusing to stop a healthy runtime is a known state'


def test_an_unconfirmed_child_still_goes_unknown() -> None:
    left_running, unknown = adapter.request_completion_state(
        kind='qwen',
        reason='RESOURCE_FLOOR',
        success=False,
        owned_exit_confirmed=False,
        controlled_remote_stop=False,
    )

    assert left_running is False
    assert unknown is True


def test_a_non_runtime_reason_still_goes_unknown_when_nothing_was_proven() -> None:
    left_running, unknown = adapter.request_completion_state(
        kind='qwen',
        reason='MODEL_REQUEST_FAILED_NO_RETRY',
        success=False,
        owned_exit_confirmed=True,
        controlled_remote_stop=False,
    )

    assert left_running is False
    assert unknown is True


def test_stopped_runtime_reports_restart_required_not_internal_error() -> None:
    # This is the "no longer INTERNAL_PRE_RESERVATION_ERROR" guarantee: a killed
    # runtime is announced as an actionable state, not as an internal fault.
    code = adapter.Runner._failure_code(RuntimeError('QWEN_RUNTIME_STOPPED_RESTART_REQUIRED'))

    assert code == 'RUNTIME_STOPPED_RESTART_REQUIRED'


def test_invalidated_binding_refuses_to_verify_the_pid_it_just_killed() -> None:
    runner = SimpleNamespace(
        runtime_binding={'pid': 1, 'executable': 'ollama.exe', 'launched_at_utc': '2026-01-01T00:00:00.0000000Z'},
        runtime_binding_invalidated=True,
    )

    with pytest.raises(RuntimeError, match='QWEN_RUNTIME_STOPPED_RESTART_REQUIRED'):
        adapter.Runner.verify_qwen_runtime_binding(runner)


def test_runtime_stopped_receipt_is_written_next_to_the_binding(tmp_path: Path) -> None:
    binding = tmp_path / 'runtime-binding-test.json'
    binding.write_text('{}', encoding='utf-8')
    runner = SimpleNamespace(
        config={'runtime_binding_path': str(binding)},
        runtime_binding_invalidated=False,
        runtime_binding=None,
        state_lock=threading.Lock(),
        accepting_requests=True,
        lock=threading.Lock(),
    )

    adapter.Runner.record_runtime_stopped(
        runner,
        reason='REMOTE_CLIENT_FAILED',
        remote_stop={'stopped': {'tree_exit_confirmed': True, 'listener_exit_confirmed': True}},
    )

    assert runner.runtime_binding_invalidated is True
    marker = tmp_path / 'runtime-binding-test.stopped.json'
    assert marker.exists()
    payload = marker.read_text(encoding='utf-8')
    assert '"format": "property-ai-runtime-stopped-v1"' in payload
    assert '"reason": "REMOTE_CLIENT_FAILED"' in payload


def test_the_stop_set_is_the_only_gate_in_the_request_loop() -> None:
    source = (ROOT / 'scripts/ops/local_ai_http.py').read_text(encoding='utf-8')

    assert "if kind=='qwen' and runtime_stop_required(reason):" in source
    assert "reason in {\n                'RESOURCE_FLOOR'" not in source
