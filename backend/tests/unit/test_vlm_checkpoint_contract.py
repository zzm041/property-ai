"""Exercise real checkpoint save/load with synthetic repository and no network."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.provider.vision import QwenVLMProvider, DevelopmentVLMProvider
from app.service.analysis import AnalysisService, AnalysisSnapshot, MediaRef
import app.provider.vision as vision


def provider(**kwargs):
    return QwenVLMProvider(base_url='https://synthetic.invalid/v1', model='fixed-model-v1',
                           api_key='synthetic-secret', timeout_seconds=1, storage=MagicMock(), **kwargs)


@pytest.fixture
def checkpoint(monkeypatch):
    monkeypatch.setattr('app.service.analysis.require_execution_lease', lambda session: None)
    monkeypatch.setattr('app.service.analysis.owns_attempt', lambda *args: True)
    s = AnalysisService.__new__(AnalysisService)
    s._session = MagicMock()
    s._session.in_transaction.return_value = False
    s._work_orders = MagicMock()
    s._ai = MagicMock()
    s._task_context = {'task_id': 'synthetic-task', 'attempt': 1}
    record = NS(id=30, tenant_id=2, property_id=3, status='PENDING_ANALYSIS', process_reason='NONE')
    s._work_orders.get_work_order_for_update.return_value = record
    logs = []
    s._work_orders.analysis_provider_checkpoints.side_effect = lambda *args: list(reversed(logs))
    s._work_orders.add_log.side_effect = logs.append
    s._vlm = provider()
    media = MediaRef('synthetic/file', 'image/png', 'IMAGE', 11, 12, 'a' * 64)
    snap = AnalysisSnapshot(work_order_id=30, tenant_id=2, property_id=3, house_id=4, building_id=5,
                            analysis_id=6, analysis_version=1, work_order_version=1,
                            input_hash='b' * 64, text='synthetic', existing_asr_text=None,
                            images=(media,), audio=())
    return s, snap, media, logs


@pytest.mark.parametrize('field,value', [
    ('prompt_version', 'different-prompt'), ('_disable_thinking', True),
    ('_max_output_tokens', 123), ('base_url', 'https://different.invalid/v1'),
    ('model_version', 'different-model'), ('model', 'different-request-model'),
])
def test_changed_inference_contract_rejects_old_media(checkpoint, field, value):
    s, snap, media, logs = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'visible defect', 'request')
    assert s._load_media_checkpoint(snap, 'VLM', media) == 'visible defect'
    setattr(s._vlm, field, value)
    assert s._load_media_checkpoint(snap, 'VLM', media) is None
    s._save_media_checkpoint(snap, 'VLM', media, 'new observation', 'request2')
    assert len(logs) == 2
    assert s._load_media_checkpoint(snap, 'VLM', media) == 'new observation'


def test_changed_response_schema_rejects_old_media(checkpoint, monkeypatch):
    s, snap, media, _ = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    monkeypatch.setattr(vision, 'vision_analysis_response_format', lambda: {'type': 'new-schema'})
    assert s._load_media_checkpoint(snap, 'VLM', media) is None


def test_prompt_text_change_cannot_hide_behind_unchanged_version(checkpoint, monkeypatch):
    s, snap, media, _ = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    monkeypatch.setattr(vision, '_VISION_PROMPT', 'changed synthetic instruction')
    assert s._load_media_checkpoint(snap, 'VLM', media) is None


def test_checkpoint_lookup_still_requires_current_attempt(checkpoint, monkeypatch):
    from app.service.task_intent import IntentLeaseLost
    s, snap, media, logs = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    monkeypatch.setattr('app.service.analysis.owns_attempt', lambda *args: False)
    with pytest.raises(IntentLeaseLost):
        s._load_media_checkpoint(snap, 'VLM', media)
    with pytest.raises(IntentLeaseLost):
        s._save_media_checkpoint(snap, 'VLM', media, 'replacement', 'request2')
    assert len(logs) == 1


def test_same_contract_reuses_without_duplicate_log_or_secret(checkpoint):
    s, snap, media, logs = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    s._vlm = provider()
    s._vlm._api_key = 'rotated-synthetic-key'
    s._vlm.timeout_seconds = 30
    assert s._load_media_checkpoint(snap, 'VLM', media) == 'observation'
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request2')
    assert len(logs) == 1
    assert 'synthetic-secret' not in str(logs[0].payload_json)
    assert 'synthetic.invalid' not in str(logs[0].payload_json)


def test_legacy_unfingerprinted_vlm_is_not_accepted(checkpoint):
    s, snap, media, logs = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    logs[0].payload_json['protocol_version'] = 'wo-media-checkpoint-v2'
    assert s._load_media_checkpoint(snap, 'VLM', media) is None


@pytest.mark.parametrize('fingerprint', [None, '', 'unknown', MagicMock()])
def test_unknown_provider_identity_never_reused_or_saved(checkpoint, fingerprint):
    s, snap, media, logs = checkpoint
    s._vlm = NS(model_version='alias', checkpoint_fingerprint=fingerprint)
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    assert not logs
    assert s._load_media_checkpoint(snap, 'VLM', media) is None


def test_changed_content_does_not_reuse(checkpoint):
    s, snap, media, _ = checkpoint
    s._save_media_checkpoint(snap, 'VLM', media, 'observation', 'request')
    changed = MediaRef(media.storage_key, media.mime_type, media.kind, media.media_id, media.file_id, 'c'*64)
    assert s._load_media_checkpoint(snap, 'VLM', changed) is None


def test_development_provider_has_stable_behavior_identity():
    first = DevelopmentVLMProvider()
    assert len(first.checkpoint_fingerprint) == 64
    assert first.checkpoint_fingerprint == DevelopmentVLMProvider().checkpoint_fingerprint
    assert first.checkpoint_fingerprint != DevelopmentVLMProvider('timeout').checkpoint_fingerprint
