"""ASR reuse contracts: exact identity, no credentials or live inference."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.provider.asr import HttpASRProvider, DevelopmentASRProvider
from app.service.analysis import MediaRef
from test_vlm_checkpoint_contract import checkpoint


def provider():
    return HttpASRProvider(base_url='https://asr.invalid/v1', model='immutable-model-v1',
                           api_key='synthetic-secret', timeout_seconds=1, storage=MagicMock())


@pytest.fixture
def audio(checkpoint):
    s, snap, _, logs = checkpoint
    s._asr = provider()
    media = MediaRef('synthetic/audio', 'audio/mpeg', 'AUDIO', 21, 22, 'a'*64)
    snap = replace(snap, images=(), audio=(media,))
    return s, snap, media, logs


@pytest.mark.parametrize('field,value', [('base_url', 'https://another.invalid/v1'),
                                       ('model', 'new-model'), ('model_version', 'new-version')])
def test_changed_asr_contract_does_not_reuse(audio, field, value):
    s, snap, media, logs = audio
    s._save_media_checkpoint(snap, 'ASR', media, 'transcript', 'req')
    assert s._load_media_checkpoint(snap, 'ASR', media) == 'transcript'
    setattr(s._asr, field, value)
    assert s._load_media_checkpoint(snap, 'ASR', media) is None
    s._save_media_checkpoint(snap, 'ASR', media, 'updated transcript', 'req2')
    assert len(logs) == 2


def test_same_asr_contract_reuses_and_secret_rotation_does_not_invalidate(audio):
    s, snap, media, logs = audio
    s._save_media_checkpoint(snap, 'ASR', media, 'transcript', 'req')
    s._asr = provider()
    s._asr._api_key = 'rotated-synthetic'
    assert s._load_media_checkpoint(snap, 'ASR', media) == 'transcript'
    assert 'synthetic-secret' not in str(logs[0].payload_json)


def legacy(audio, contract=None):
    s, snap, media, logs = audio
    snap = replace(snap, attachment_completion=True, source_analysis_id=8,
                   source_analysis_version=1, source_input_hash='b'*64,
                   original_input_hash='b'*64, existing_asr_text='transcript')
    payload = dict(schema_version=1, analysis_id=8, analysis_version=1, input_hash='b'*64,
                   provider_type='ASR', model_version=s._asr.model_version,
                   value='transcript', value_sha256=sha256(b'transcript').hexdigest())
    if contract is not None:
        payload['provider_contract'] = contract
    logs.append(NS(payload_json=payload))
    return s, snap, media


def test_legacy_without_contract_cannot_bypass_new_media_contract(audio):
    s, snap, media = legacy(audio)
    assert s._load_legacy_first_audio_checkpoint(snap, media) is None


def test_proven_legacy_contract_can_upgrade_but_endpoint_change_cannot(audio):
    s, _, _, _ = audio
    s, snap, media = legacy(audio, s._asr.checkpoint_fingerprint)
    assert s._load_legacy_first_audio_checkpoint(snap, media) == 'transcript'
    s._asr.base_url = 'https://changed.invalid/v1'
    assert s._load_legacy_first_audio_checkpoint(snap, media) is None


def test_old_unfingerprinted_media_is_not_a_match(audio):
    s, snap, media, logs = audio
    s._save_media_checkpoint(snap, 'ASR', media, 'transcript', 'req')
    logs[0].payload_json['protocol_version'] = 'wo-media-checkpoint-v2'
    assert s._load_media_checkpoint(snap, 'ASR', media) is None


def test_unknown_asr_does_not_persist_reusable_checkpoint(audio):
    s, snap, media, logs = audio
    s._asr = NS(model_version='alias')
    s._save_media_checkpoint(snap, 'ASR', media, 'transcript', 'req')
    assert not logs


def test_development_asr_has_stable_identity():
    assert DevelopmentASRProvider().checkpoint_fingerprint == DevelopmentASRProvider().checkpoint_fingerprint
    assert DevelopmentASRProvider().checkpoint_fingerprint != DevelopmentASRProvider('timeout').checkpoint_fingerprint


@pytest.mark.parametrize('kind', ['ASR', 'VLM'])
def test_aggregate_service_checkpoint_is_fenced_by_contract(audio, kind):
    s, snap, _, logs = audio
    s._work_orders.latest_analysis_provider_checkpoint.side_effect = lambda *args: logs[-1] if logs else None
    s._save_provider_checkpoint(snap, kind, 'successful partial', 'req')
    assert s._load_provider_checkpoint(snap, kind) == 'successful partial'
    active = s._asr if kind == 'ASR' else s._vlm
    active.base_url = 'https://changed.invalid/v1'
    assert s._load_provider_checkpoint(snap, kind) is None
    s._save_provider_checkpoint(snap, kind, 'new partial', 'req2')
    assert len(logs) == 2
    assert s._load_provider_checkpoint(snap, kind) == 'new partial'
