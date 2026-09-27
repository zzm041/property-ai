"""Optional bounded inference controls preserve default provider semantics."""
import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.provider.factory import build_llm_provider, build_vlm_provider
from app.provider.llm import QwenLLMProvider
from app.provider.vision import QwenVLMProvider
from app.provider.storage import DevelopmentStorageProvider
from app.domain.vision import VISION_ANALYSIS_PROMPT_VERSION, vision_analysis_response_format


@pytest.mark.parametrize("vision", [False, True])
@pytest.mark.parametrize("bounded", [False, True])
def test_request_controls_are_explicit_and_default_payload_unchanged(vision, bounded):
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == (
            vision_analysis_response_format() if vision else {"type": "json_object"}
        )
        assert body["temperature"] == 0
        if bounded:
            assert body["reasoning_effort"] == "none"
            assert body["max_tokens"] == 512
        else:
            assert "reasoning_effort" not in body and "max_tokens" not in body
        if vision:
            content = body["messages"][0]["content"]
            assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
            assert '可直接观察到' in content[0]['text']
            assert request.headers["X-Prompt-Version"] == VISION_ANALYSIS_PROMPT_VERSION
            assert QwenVLMProvider.prompt_version == VISION_ANALYSIS_PROMPT_VERSION
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"description": "synthetic", "labels": [], "confidence": 0.5})}}]})

    storage = DevelopmentStorageProvider()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        options = dict(base_url="http://127.0.0.1:11435/v1", model="qwen3.5:2b", api_key="synthetic-test-only", timeout_seconds=2, client=client)
        if bounded:
            options.update(disable_thinking=True, max_output_tokens=512)
        if vision:
            item = storage.register_object(mime_type="image/png", size_bytes=3, tenant_id=51, property_id=61)
            storage.put_object(item.storage_key, b"png", "image/png")
            result = QwenVLMProvider(**options, storage=storage).inspect_image(storage_key=item.storage_key, mime_type="image/png")
            assert result.description == "synthetic"
        else:
            result = QwenLLMProvider(**options).structured_completion(prompt="synthetic", prompt_version="test")
            assert result["description"] == "synthetic"


@pytest.mark.parametrize("prefix", ["llm", "vlm"])
@pytest.mark.parametrize("value", [0, -1, 8193])
def test_settings_reject_invalid_output_limits(prefix, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{f"{prefix}_max_output_tokens": value})


@pytest.mark.parametrize("prefix", ["llm", "vlm"])
def test_factory_passes_controls_only_when_explicitly_configured(prefix):
    settings = Settings(_env_file=None, app_env="test", **{
        f"{prefix}_provider": "production", f"{prefix}_base_url": "http://127.0.0.1:11435/v1",
        f"{prefix}_api_key": "synthetic-local-only", f"{prefix}_model": "qwen3.5:2b",
        f"{prefix}_disable_thinking": True, f"{prefix}_max_output_tokens": 512,
    })
    provider = build_llm_provider(settings) if prefix == "llm" else build_vlm_provider(settings)
    assert provider._disable_thinking is True
    assert provider._max_output_tokens == 512


def test_default_settings_do_not_switch_providers_or_controls():
    settings = Settings(_env_file=None)
    assert settings.llm_provider == settings.vlm_provider == "development"
    assert settings.llm_disable_thinking is settings.vlm_disable_thinking is False
    assert settings.llm_max_output_tokens is settings.vlm_max_output_tokens is None


def test_vlm_rejects_saved_nested_json_wrapper_without_recursive_rewrite():
    storage = DevelopmentStorageProvider()
    item = storage.register_object(mime_type="image/png", size_bytes=3, tenant_id=51, property_id=61)
    storage.put_object(item.storage_key, b"png", "image/png")

    def handler(_request):
        nested = {"JSON": json.dumps({"description": "蓝色闸阀", "labels": ["闸阀"], "confidence": 0.95})}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(nested)}, "finish_reason": "stop"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = QwenVLMProvider(
            base_url="http://127.0.0.1:11435/v1",
            model="qwen3.5:4b",
            api_key="synthetic-local-only",
            timeout_seconds=2,
            storage=storage,
            client=client,
        )
        from app.provider.errors import ProviderInvalidOutputError

        with pytest.raises(ProviderInvalidOutputError):
            provider.inspect_image(storage_key=item.storage_key, mime_type="image/png")
