"""Approved collector's newly required boundaries, zero model/network requests."""
import json
from pathlib import Path
import runpy
import pytest

h = runpy.run_path(str(Path(__file__).with_name('run-approved-supplement.py')))
check, append, next_number = h['raw_contract'], h['append_file'], h['next_number']


def response(value, finish='stop'):
    return json.dumps({'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(value)}}]})


def value():
    return dict(category='electrical', subcategory='outlet', severity='low', priority='p3',
                summary='插座没电，无烟', risk='electric', required_role='STAFF', required_skill='maintenance', confidence=.8)


def test_raw_contract_never_accepts_http_only_fences_truncation_or_invalid_enums():
    assert check(response(value()), 'LLM') == []
    for raw in ['<html>bad</html>', response({**value(), 'category': 'gas'}),
                response({**value(), 'confidence': '0.8'}), response(value(), 'length'),
                response({**value(), 'summary': 'outlet failed'}), response({**value(), 'extra': 1})]:
        assert check(raw, 'LLM')


def test_vision_strict_types_and_finite_confidence():
    assert not check(response(dict(description='插座', labels=['插座'], confidence=.8)), 'VLM')
    for confidence in [True, '0.8', float('nan'), 1.1]:
        assert check(response(dict(description='插座', labels=['插座'], confidence=confidence)), 'VLM')


def save_stage(root, n, started=100):
    folder = root / f'request-{n:02d}'; folder.mkdir()
    append(folder / 'reservation.json', {'number': n, 'reserved_unix': started})
    return folder


def test_reserved_but_unknown_response_is_never_replayed(tmp_path):
    assert next_number(tmp_path, now=100) == 1
    save_stage(tmp_path, 1)
    with pytest.raises(RuntimeError, match='UNKNOWN'): next_number(tmp_path, now=110)


def test_semantic_gate_requires_actual_response_binding(tmp_path):
    folder = save_stage(tmp_path, 1)
    append(folder / 'response.json', {'raw': 'unaltered'})
    with pytest.raises(RuntimeError, match='SEMANTIC'): next_number(tmp_path, now=110)
    append(folder / 'judgement.json', {'continue': True, 'response_sha256': 'wrong'})
    with pytest.raises(RuntimeError, match='NOT_ACCEPTED'): next_number(tmp_path, now=110)


def test_acceptance_only_opens_next_request_and_wall_window_never_resets(tmp_path):
    folder = save_stage(tmp_path, 1)
    append(folder / 'response.json', {'raw': 'unaltered'})
    append(folder / 'judgement.json', {'continue': True, 'response_sha256': h['sha'](folder / 'response.json')})
    assert next_number(tmp_path, now=110) == 2
    with pytest.raises(RuntimeError, match='WALL'): next_number(tmp_path, now=1900)
    assert h['remaining_seconds'](100, 1900) == 0


def test_32_is_hard_limit_even_with_good_results(tmp_path):
    for n in range(1, 33): save_stage(tmp_path, n)
    with pytest.raises(RuntimeError, match='REQUEST_LIMIT'): next_number(tmp_path, now=100)


def test_critical_stop_prevents_additional_reservation(tmp_path):
    append(tmp_path / 'stop.json', {'reason': 'critical'})
    with pytest.raises(RuntimeError, match='STOPPED'): next_number(tmp_path, now=100)


def test_evidence_never_overwritten(tmp_path):
    append(tmp_path / 'original.json', {'raw': 'original'})
    with pytest.raises(FileExistsError): append(tmp_path / 'original.json', {'raw': 'new'})


def test_wall_timeout_terminates_only_supplied_child_without_retry():
    from unittest.mock import MagicMock
    process, fault = MagicMock(), MagicMock()
    process.poll.return_value = None; fault.is_set.return_value = False
    assert h['wait_single_child'](process, 60, fault, clock=lambda: 60) == 'REQUEST_WALL_TIMEOUT_MODEL_END_UNKNOWN'
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=3)


def test_resource_failure_terminates_child_and_prevents_further_wait():
    from unittest.mock import MagicMock
    process, fault = MagicMock(), MagicMock()
    process.poll.return_value = None; fault.is_set.return_value = True
    assert h['wait_single_child'](process, 60, fault, clock=lambda: 2) == 'RESOURCE_FLOOR_OR_OBSERVATION_FAILED'
    process.terminate.assert_called_once()


def test_completed_child_is_not_terminated():
    from unittest.mock import MagicMock
    process = MagicMock(); process.poll.return_value = 0
    assert h['wait_single_child'](process, 60, MagicMock(), clock=lambda: 61) is None
    process.terminate.assert_not_called()


def test_frozen_text_child_uses_mock_transport_and_saves_original_response(tmp_path, monkeypatch):
    import httpx
    import app.provider.factory  # Resolve Client annotations before replacing constructor.
    original_client = httpx.Client
    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(lambda request: httpx.Response(200, text=response(value())))
        return original_client(*args, **kwargs)
    monkeypatch.setattr(httpx, 'Client', factory)
    g = h['child'].__globals__
    # frozen() keeps the actual approved inputs; only output is a fresh pytest path.
    root, manifest = h['frozen']()
    monkeypatch.setitem(g, 'frozen', lambda: (root, manifest))
    monkeypatch.setitem(g, 'OUT', tmp_path)
    folder = tmp_path / 'request-01'; folder.mkdir()
    append(folder / 'reservation.json', {'number': 1})
    h['child'](1)
    result = h['read'](folder / 'response.json')
    assert result['contract_errors'] == []
    assert result['provider_result'][0]['summary'] == '插座没电，无烟'
    assert h['read'](folder / 'request.json')['body']['max_tokens'] == 512
    assert result['prompt_version'] == 'wo-analysis-v8-maintenance'
