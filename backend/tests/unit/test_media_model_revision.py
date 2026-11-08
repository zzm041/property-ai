"""Deployment revision fences successful media reuse; no inference/network."""
import hashlib
import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.provider.asr import HttpASRProvider
from app.provider.factory import build_asr_provider, build_vlm_provider
from app.provider.vision import QwenVLMProvider
from test_asr_checkpoint_contract import audio
from test_vlm_checkpoint_contract import checkpoint


@pytest.mark.parametrize('kind', ['VLM', 'ASR'])
def test_revision_enters_factory_and_reuse_contract(kind):
    prefix=kind.lower()
    settings=Settings(_env_file=None, **{prefix+'_provider':'production',prefix+'_model_revision':'sha256:synthetic-a'})
    build=build_vlm_provider if kind=='VLM' else build_asr_provider
    first=build(settings,storage=MagicMock())
    assert first.model_revision=='sha256:synthetic-a'
    assert first.model==getattr(settings,prefix+'_model')
    assert first.checkpoint_fingerprint==build(settings,storage=MagicMock()).checkpoint_fingerprint
    changed=build(settings.model_copy(update={prefix+'_model_revision':'sha256:synthetic-b'}),storage=MagicMock())
    assert changed.checkpoint_fingerprint!=first.checkpoint_fingerprint


@pytest.mark.parametrize('field', ['vlm_model_revision', 'asr_model_revision'])
@pytest.mark.parametrize('value', ['x'*129, ' contains whitespace', 'line\nbreak', 'key=value'])
def test_revision_rejects_unbounded_or_ambiguous_label(field,value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field:value})


@pytest.mark.parametrize('kind', ['VLM', 'ASR'])
def test_revision_rejects_existing_media_and_aggregate_checkpoint(audio,kind):
    service,snap,media,logs=audio
    active=service._vlm if kind=='VLM' else service._asr
    active.model_revision='deployment-a'
    service._save_media_checkpoint(snap,kind,media,'successful observation','synthetic')
    assert service._load_media_checkpoint(snap,kind,media)=='successful observation'
    active.model_revision='deployment-b'
    assert service._load_media_checkpoint(snap,kind,media) is None
    service._work_orders.latest_analysis_provider_checkpoint.side_effect=lambda *args:logs[-1] if logs else None
    service._save_provider_checkpoint(snap,kind,'new successful observation','synthetic')
    assert service._load_provider_checkpoint(snap,kind)=='new successful observation'
    active.model_revision='deployment-c'
    assert service._load_provider_checkpoint(snap,kind) is None


def test_unspecified_asr_revision_preserves_existing_contract():
    provider=HttpASRProvider(base_url='https://synthetic.invalid/v1',model='old-alias',api_key='synthetic',timeout_seconds=1,storage=MagicMock())
    legacy={'adapter':'http-asr-transcription-v1','endpoint':provider.base_url,
            'model':provider.model,'model_version':provider.model_version}
    assert provider.checkpoint_fingerprint==hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).hexdigest()


def test_unspecified_vlm_revision_preserves_existing_contract():
    import app.provider.vision as vision
    provider=QwenVLMProvider(base_url='https://synthetic.invalid/v1',model='old-alias',api_key='synthetic',timeout_seconds=1,storage=MagicMock())
    legacy={'adapter':'qwen-vision-v1','endpoint':provider.base_url,'model':provider.model,
            'model_version':provider.model_version,'prompt_version':provider.prompt_version,
            'prompt':vision._VISION_PROMPT,'generation':provider._generation_contract()}
    assert provider.checkpoint_fingerprint==vision._contract_digest(legacy)
