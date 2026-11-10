"""PA-140: the shared-token gate belongs to the credentials, not to the main LLM.

`_reject_shared_token_off_host()` used to be called from inside
`if app_env == "staging" and llm_provider == "production":`, so a staging box that only
used a production *vision* provider (LLM_PROVIDER=development, VLM_PROVIDER=production)
never ran the gate at all and shipped the adapter token to VLM_BASE_URL. These cases pin
the unconditional call, and the all-development box that must keep starting.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

SHARED = "shared-adapter-token-0123456789abcdefghijklmno"
OTHER = "provider-specific-key-0123456789abcdefghijklm"
REMOTE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

BASE = {
    "app_env": "staging",
    "app_secret_key": "pa140-secret-key-not-the-default-value",
    "app_debug": False,
    "wechat_login_enabled": False,
    # The shape that used to slip through: no production LLM at all.
    "llm_provider": "development",
    "local_ai_internal_token": SHARED,
    "rag_llm_provider": "development",
    "vlm_provider": "development",
    "asr_provider": "development",
    "embedding_provider": "development",
    "reranker_provider": "development",
    "supervisor_agent_provider": "development",
}


def _settings(**overrides):
    payload = dict(BASE)
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


MESSAGE = "would send the shared LOCAL_AI_INTERNAL_TOKEN"


def test_a_staging_box_with_only_a_production_vlm_is_still_refused():
    """The exact gap PA-140 was opened for."""

    with pytest.raises(ValueError, match="VLM would send the shared") as raised:
        _settings(
            vlm_provider="production",
            vlm_base_url=REMOTE,
            vlm_api_key=SHARED,
        )

    assert "dashscope.aliyuncs.com" in str(raised.value)


@pytest.mark.parametrize(
    "family, overrides",
    [
        (
            "ASR",
            {
                "asr_provider": "production",
                "asr_base_url": REMOTE,
                "asr_api_key": SHARED,
            },
        ),
        (
            "Embedding",
            {
                "embedding_provider": "production",
                "embedding_endpoint": REMOTE,
                "embedding_api_key": SHARED,
            },
        ),
        (
            "Reranker",
            {
                "reranker_provider": "production",
                "reranker_endpoint": REMOTE,
                "reranker_api_key": SHARED,
            },
        ),
        (
            "Supervisor agent",
            {
                "supervisor_agent_provider": "production",
                "supervisor_agent_base_url": REMOTE,
                "supervisor_agent_api_key": SHARED,
            },
        ),
        (
            "RAG LLM",
            {
                "rag_llm_provider": "production",
                "rag_llm_base_url": REMOTE,
                "rag_llm_api_key": SHARED,
            },
        ),
    ],
)
def test_every_family_is_gated_regardless_of_the_main_llm(family, overrides):
    with pytest.raises(ValueError, match=f"{family} would send the shared"):
        _settings(**overrides)


@pytest.mark.parametrize("app_env", ["development", "staging", "test"])
def test_the_gate_is_not_environment_bound(app_env):
    with pytest.raises(ValueError, match=MESSAGE):
        _settings(
            app_env=app_env,
            vlm_provider="production",
            vlm_base_url=REMOTE,
            vlm_api_key=SHARED,
        )


def test_an_all_development_box_keeps_starting():
    settings = _settings()

    assert settings.app_env == "staging"
    assert settings.local_ai_internal_token.get_secret_value() == SHARED


def test_production_families_with_their_own_keys_keep_starting():
    settings = _settings(
        vlm_provider="production",
        vlm_base_url=REMOTE,
        vlm_api_key=OTHER,
        asr_provider="production",
        asr_base_url=REMOTE,
        asr_api_key=OTHER,
    )

    assert settings.vlm_api_key.get_secret_value() == OTHER


def test_a_production_family_on_a_local_endpoint_keeps_starting():
    settings = _settings(
        vlm_provider="production",
        vlm_base_url="http://host.docker.internal:11437/v1",
        vlm_api_key=SHARED,
    )

    assert settings.vlm_base_url == "http://host.docker.internal:11437/v1"


def test_without_the_shared_token_the_gate_is_a_no_op():
    settings = _settings(
        local_ai_internal_token="",
        vlm_provider="production",
        vlm_base_url=REMOTE,
        vlm_api_key=SHARED,
    )

    assert settings.vlm_provider == "production"
