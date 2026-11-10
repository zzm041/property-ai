"""PA-139: the supervisor planner is the seventh family that sends the adapter token.

`app/provider/supervisor_agent.py:55-57` builds its own `QwenLLMProvider` straight from
`supervisor_agent_api_key` / `supervisor_agent_base_url`. `_reject_shared_token_off_host`
knew six families (LLM, RAG LLM, VLM, ASR, Embedding, Reranker) and therefore let a box
configured with `SUPERVISOR_AGENT_PROVIDER=production` ship the shared
`LOCAL_AI_INTERNAL_TOKEN` to any endpoint. These cases pin the seventh family.
"""

from __future__ import annotations

import pytest

from app.core.config import LOCAL_AI_ENDPOINT_HOSTS, Settings

SHARED = "shared-adapter-token-0123456789abcdefghijklmno"
OTHER = "provider-specific-key-0123456789abcdefghijklm"
REMOTE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

BASE = {
    "app_env": "production",
    "app_secret_key": "pa139-secret-key-not-the-default-value",
    "app_debug": False,
    "wechat_login_enabled": False,
    # Keep the main LLM quiet so a failure can only come from the supervisor entry.
    "llm_provider": "development",
    "local_ai_internal_token": SHARED,
    "rag_llm_provider": "inherit",
    "vlm_provider": "development",
    "asr_provider": "development",
    "embedding_provider": "development",
    "reranker_provider": "development",
    "supervisor_agent_enabled": True,
    "supervisor_agent_provider": "production",
    "supervisor_agent_base_url": REMOTE,
    "supervisor_agent_api_key": SHARED,
    "file_scanner_provider": "clamav",
    "file_scanner_host": "127.0.0.1",
}


def _settings(**overrides):
    payload = dict(BASE)
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


MESSAGE = "Supervisor agent would send the shared LOCAL_AI_INTERNAL_TOKEN"


def test_the_supervisor_planner_refuses_the_shared_token_on_a_remote_endpoint():
    with pytest.raises(ValueError, match=MESSAGE):
        _settings()


def test_the_message_names_the_supervisor_family_and_the_host():
    with pytest.raises(ValueError) as raised:
        _settings()

    message = str(raised.value)
    assert "Supervisor agent" in message
    assert "dashscope.aliyuncs.com" in message
    assert "give this provider its own key" in message


@pytest.mark.parametrize("host", sorted(LOCAL_AI_ENDPOINT_HOSTS))
def test_a_local_endpoint_may_hold_it_on_every_alias(host):
    settings = _settings(supervisor_agent_base_url=f"http://{host}:11437/v1")

    assert settings.supervisor_agent_api_key.get_secret_value() == SHARED


def test_a_provider_specific_supervisor_key_may_point_anywhere():
    settings = _settings(supervisor_agent_api_key=OTHER)

    assert settings.supervisor_agent_base_url == REMOTE


def test_the_development_planner_never_sends_a_key():
    """The stub planner returns before any HTTP call, so its key is never used."""

    settings = _settings(supervisor_agent_provider="development")

    assert settings.supervisor_agent_base_url == REMOTE


def test_an_empty_supervisor_key_has_nothing_to_leak():
    settings = _settings(supervisor_agent_api_key="")

    assert settings.supervisor_agent_api_key.get_secret_value() == ""


def test_without_the_shared_token_there_is_nothing_to_leak():
    settings = _settings(local_ai_internal_token="")

    assert settings.supervisor_agent_api_key.get_secret_value() == SHARED


def test_the_guard_covers_all_seven_families():
    """One refusal per family, each naming itself — six from PA-137 plus supervisor."""

    families = {
        "LLM": {
            "llm_provider": "production",
            "llm_base_url": REMOTE,
            "llm_api_key": SHARED,
        },
        "RAG LLM": {
            "rag_llm_provider": "production",
            "rag_llm_base_url": REMOTE,
            "rag_llm_api_key": SHARED,
        },
        "VLM": {
            "vlm_provider": "production",
            "vlm_base_url": REMOTE,
            "vlm_api_key": SHARED,
        },
        "ASR": {
            "asr_provider": "production",
            "asr_base_url": REMOTE,
            "asr_api_key": SHARED,
        },
        "Embedding": {
            "embedding_provider": "production",
            "embedding_endpoint": REMOTE,
            "embedding_api_key": SHARED,
        },
        "Reranker": {
            "reranker_provider": "production",
            "reranker_endpoint": REMOTE,
            "reranker_api_key": SHARED,
        },
        "Supervisor agent": {
            "supervisor_agent_provider": "production",
            "supervisor_agent_base_url": REMOTE,
            "supervisor_agent_api_key": SHARED,
        },
    }

    for label, overrides in families.items():
        quiet = {
            "llm_provider": "development",
            "llm_base_url": "",
            "llm_api_key": "",
            "rag_llm_provider": "development",
            "rag_llm_base_url": "",
            "rag_llm_api_key": "",
            "vlm_provider": "development",
            "vlm_base_url": "",
            "vlm_api_key": "",
            "asr_provider": "development",
            "asr_base_url": "",
            "asr_api_key": "",
            "embedding_provider": "development",
            "embedding_endpoint": "",
            "embedding_api_key": "",
            "reranker_provider": "development",
            "reranker_endpoint": "",
            "reranker_api_key": "",
            "supervisor_agent_provider": "development",
            "supervisor_agent_base_url": "",
            "supervisor_agent_api_key": "",
        }
        quiet.update(overrides)
        with pytest.raises(ValueError, match=f"{label} would send the shared") as raised:
            _settings(**quiet)
        assert label in str(raised.value)
