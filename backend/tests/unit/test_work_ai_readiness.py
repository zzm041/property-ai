"""Regression for WO23: healthy API must not conceal an empty AI credential."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def helper():
    spec = importlib.util.spec_from_file_location('work_ai_readiness', ROOT / 'scripts/ops/work_ai_readiness.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe():
    digest = hashlib.sha256(b'synthetic-local-token-for-test-only').hexdigest()
    return {'provider': 'production', 'base_url': 'http://host.docker.internal:11437/v1',
            'model': 'qwen38-27b-q3km-g55:7af3b8f', 'key_present': True, 'key_sha256': digest}


def test_wo23_missing_credential_is_rejected_before_phone_entry():
    actual = probe()
    actual.update(key_present=False, key_sha256=hashlib.sha256(b'').hexdigest())
    with pytest.raises(ValueError, match='WORK_AI_CREDENTIAL_MISSING'):
        helper().require_credentials(actual, probe()['key_sha256'])


@pytest.mark.parametrize('field,value', [('key_sha256', '0' * 64),
    ('base_url', 'https://external.invalid/v1'), ('provider', 'development'), ('model', 'other')])
def test_wrong_binding_cannot_pass(field, value):
    actual = probe(); actual[field] = value
    with pytest.raises(ValueError):
        helper().require_credentials(actual, probe()['key_sha256'])


def test_matching_local_credential_passes_without_changing_probe():
    actual = probe(); before = copy.deepcopy(actual)
    helper().require_credentials(actual, actual['key_sha256'])
    assert actual == before


def test_inject_only_missing_work_credentials_and_preserve_every_other_setting():
    module = helper()
    env = ['DATABASE_URL=synthetic', 'LLM_PROVIDER=production',
           'LLM_BASE_URL=http://host.docker.internal:11437/v1', 'LLM_API_KEY=',
           'VLM_BASE_URL=http://host.docker.internal:11437/v1', 'VLM_API_KEY=',
           'ASR_BASE_URL=http://host.docker.internal:11437/v1', 'ASR_API_KEY=', 'OTHER=value']
    changed = module.inject_missing_credentials(env, 'synthetic-secret-for-this-test-only')
    for old, new in zip(env, changed, strict=True):
        key = old.split('=', 1)[0]
        assert new == (key + '=synthetic-secret-for-this-test-only' if key in module.WORK_KEYS else old)
    assert env[3] == 'LLM_API_KEY='
    with pytest.raises(ValueError, match='EXISTING_CREDENTIAL'):
        module.inject_missing_credentials(changed, 'synthetic-secret-for-this-test-only')
    with pytest.raises(ValueError):
        module.inject_missing_credentials(env, '')
    with pytest.raises(ValueError):
        module.inject_missing_credentials(env + ['LLM_API_KEY='], 'synthetic-secret-for-this-test-only')


@pytest.mark.parametrize('status,scope,accepting,gate,passes', [
    (200, 'work_order_only', True, 'READY', True),
    (401, 'work_order_only', True, 'READY', False),
    (200, 'rag_only', True, 'READY', False),
    (200, 'work_order_only', False, 'READY', False),
    (200, 'work_order_only', True, 'EXHAUSTED', False),
])
def test_container_health_cannot_be_replaced_by_host_health(monkeypatch, status, scope, accepting, gate, passes):
    module = helper()
    response = probe()
    response.update(http_status=status, health={'scope': scope, 'accepting_requests': accepting, 'reservation_gate': gate})
    def execute(args, **kwargs):
        assert args[:4] == ['docker', 'exec', '-i', 'exact-container']
        assert args[-1] == 'health'
        assert 'synthetic-local-token-for-test-only' not in args
        return SimpleNamespace(returncode=0, stdout=json.dumps(response).encode())
    monkeypatch.setattr(module.subprocess, 'run', execute)
    if passes:
        assert module.probe_container('exact-container', 'synthetic-local-token-for-test-only', health=True) == {
            'credential_matches': True, 'container_connection_ready': True}
    else:
        with pytest.raises(ValueError, match='WORK_AI_CONTAINER_CONNECTION_NOT_READY'):
            module.probe_container('exact-container', 'synthetic-local-token-for-test-only', health=True)
