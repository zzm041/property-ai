"""PA-128: the work-order LLM falls back to LOCAL_AI_INTERNAL_TOKEN like RAG does.

Three credential shapes are pinned, plus the guard that keeps the internal token
away from a non-local endpoint:

- explicit `LLM_API_KEY` wins, the local token never overrides it;
- only the local token present + a loopback/host.docker.internal endpoint -> the
  provider puts `Bearer <token>` on the wire (before PA-128 it sent nothing and
  raised before any request);
- neither present -> the provider still fails closed (`ProviderUnavailableError`),
  it does not invent a credential;
- local token present but the endpoint is not this host -> no fallback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.provider.errors import ProviderUnavailableError
from app.provider.factory import build_llm_provider
from app.provider.llm import QwenLLMProvider

LOCAL_TOKEN = "local-internal-token-43-chars-long-xxxxxxxxxxx"
LOCAL_ENDPOINT = "http://host.docker.internal:11437/v1"
REMOTE_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT_VERSION = "wo-analysis-v26-summary-fidelity"


def _settings(*, api_key: str, local_token: str, base_url: str = LOCAL_ENDPOINT) -> Settings:
    return Settings(_env_file=None).model_copy(
        update={
            "app_env": "staging",
            "llm_provider": "production",
            "llm_base_url": base_url,
            "llm_api_key": SecretStr(api_key),
            "local_ai_internal_token": SecretStr(local_token),
        }
    )


def _bearer(provider: QwenLLMProvider) -> str:
    """Return the Authorization header the provider would send."""

    captured: dict[str, object] = {}

    def fake_request_json(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {"message": {"content": "{}"}, "finish_reason": "stop"}
            ]
        }

    with patch("app.provider.llm.request_json", fake_request_json):
        provider.structured_completion(prompt="ping", prompt_version=PROMPT_VERSION)
    return str((captured["headers"] or {})["Authorization"])


def test_explicit_key_wins_over_the_local_token() -> None:
    settings = _settings(api_key="sk-explicit", local_token=LOCAL_TOKEN)

    provider = build_llm_provider(settings)
    assert isinstance(provider, QwenLLMProvider)
    assert provider._api_key == "sk-explicit"
    assert _bearer(provider) == "Bearer sk-explicit"
    assert settings.ai_credentials()["sources"]["resolved_from"] == "llm_api_key"
    assert settings.ai_credentials()["fallback_to_local_token"] is False


def test_local_token_alone_reaches_the_wire_for_a_local_endpoint() -> None:
    settings = _settings(api_key="", local_token=LOCAL_TOKEN)

    provider = build_llm_provider(settings)
    assert provider._api_key == LOCAL_TOKEN
    assert _bearer(provider) == f"Bearer {LOCAL_TOKEN}"
    # /readyz 不再一边说缺 key 一边能跑分析
    assert settings.ai_credentials()["status"] == "configured"
    assert settings.ai_credentials()["missing"] == []
    assert settings.ai_credentials()["sources"]["resolved_from"] == "local_ai_internal_token"
    assert settings.ai_credentials()["fallback_to_local_token"] is True


def test_both_empty_still_fails_closed() -> None:
    settings = _settings(api_key="", local_token="")

    provider = build_llm_provider(settings)
    assert provider._api_key == ""
    with pytest.raises(ProviderUnavailableError):
        _bearer(provider)  # 不发请求就失败，不会凭空造凭证
    assert settings.ai_credentials()["status"] == "missing"
    assert settings.ai_credentials()["missing"] == ["LLM_API_KEY"]
    assert settings.ai_credentials()["sources"]["resolved_from"] == "none"
    assert settings.ai_credentials()["fallback_to_local_token"] is False


def test_local_token_is_not_used_for_a_non_local_endpoint() -> None:
    settings = _settings(api_key="", local_token=LOCAL_TOKEN, base_url=REMOTE_ENDPOINT)

    provider = build_llm_provider(settings)
    assert provider._api_key == ""
    assert settings.ai_credentials()["status"] == "missing"
    assert settings.ai_credentials()["fallback_to_local_token"] is False
