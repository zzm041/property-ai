"""PA-137: the shared adapter token must never be put on a remote connection.

`.env.delivery` sets `LLM_API_KEY` to the same 43-character value as
`LOCAL_AI_INTERNAL_TOKEN`. `resolve_local_ai_key` only *withholds* the token from a
remote endpoint — it happily returns an explicit key that happens to be the token, so
a box whose endpoint pointed somewhere else would send the shared secret off-host and
still pass every startup check. These cases pin the guard that closes that hole, and
the delivery shape that must keep starting.
"""

from __future__ import annotations

import pytest

from app.core.config import LOCAL_AI_ENDPOINT_HOSTS, Settings

SHARED = "shared-adapter-token-0123456789abcdefghijklmno"
OTHER = "provider-specific-key-0123456789abcdefghijklm"
REMOTE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LOCAL = "http://127.0.0.1:11437/v1"

BASE = {
    "app_env": "staging",
    "app_secret_key": "pa137-secret-key-not-the-default-value",
    "app_debug": False,
    "wechat_login_enabled": False,
    "llm_provider": "production",
    "llm_base_url": REMOTE,
    "llm_api_key": SHARED,
    "local_ai_internal_token": SHARED,
    "rag_llm_provider": "inherit",
    "vlm_provider": "development",
    "asr_provider": "development",
    "embedding_provider": "development",
    "reranker_provider": "development",
}


def _settings(**overrides):
    payload = dict(BASE)
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


MESSAGE = "only sent to a local endpoint"


def test_staging_refuses_the_shared_token_pointed_at_a_remote_endpoint():
    with pytest.raises(ValueError, match=MESSAGE):
        _settings()


@pytest.mark.parametrize("host", sorted(LOCAL_AI_ENDPOINT_HOSTS))
def test_the_delivery_shape_keeps_starting_on_every_local_alias(host):
    """key == token is the delivery configuration; only a local host may hold it."""

    settings = _settings(llm_base_url=f"http://{host}:11437/v1")

    assert settings.llm_base_url == f"http://{host}:11437/v1"
    assert settings.resolve_local_ai_key(
        settings.llm_api_key, settings.llm_base_url
    ) == SHARED


def test_a_provider_specific_key_may_point_anywhere():
    settings = _settings(llm_api_key=OTHER)

    assert settings.llm_base_url == REMOTE


def test_without_the_shared_token_there_is_nothing_to_leak():
    settings = _settings(local_ai_internal_token="")

    assert settings.llm_api_key.get_secret_value() == SHARED


def test_production_refuses_it_too():
    with pytest.raises(ValueError, match=MESSAGE):
        _settings(app_env="production")


def test_production_keeps_starting_on_the_local_adapter():
    settings = _settings(app_env="production", llm_base_url=LOCAL)

    assert settings.app_env == "production"


def test_production_keeps_starting_with_its_own_key():
    settings = _settings(app_env="production", llm_api_key=OTHER)

    assert settings.llm_provider == "production"


def test_rag_inheriting_the_llm_pair_is_covered_by_the_llm_rule():
    """`inherit` reuses the LLM pair, so the LLM entry reports it once, not twice."""

    with pytest.raises(ValueError, match=MESSAGE) as excinfo:
        _settings()

    assert "LLM would send" in str(excinfo.value)
    assert "RAG" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "provider_key", "endpoint_key", "api_key_key"),
    [
        ("RAG LLM", "rag_llm_provider", "rag_llm_base_url", "rag_llm_api_key"),
        ("VLM", "vlm_provider", "vlm_base_url", "vlm_api_key"),
        ("ASR", "asr_provider", "asr_base_url", "asr_api_key"),
        ("Embedding", "embedding_provider", "embedding_endpoint", "embedding_api_key"),
        ("Reranker", "reranker_provider", "reranker_endpoint", "reranker_api_key"),
    ],
)
def test_every_provider_family_is_guarded(label, provider_key, endpoint_key, api_key_key):
    overrides = {
        "llm_api_key": OTHER,
        provider_key: "production",
        endpoint_key: REMOTE,
        api_key_key: SHARED,
    }

    with pytest.raises(ValueError, match=MESSAGE) as excinfo:
        _settings(**overrides)

    assert f"{label} would send" in str(excinfo.value)
    assert "dashscope.aliyuncs.com" in str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "provider_key", "endpoint_key", "api_key_key"),
    [
        ("RAG LLM", "rag_llm_provider", "rag_llm_base_url", "rag_llm_api_key"),
        ("VLM", "vlm_provider", "vlm_base_url", "vlm_api_key"),
        ("ASR", "asr_provider", "asr_base_url", "asr_api_key"),
        ("Embedding", "embedding_provider", "embedding_endpoint", "embedding_api_key"),
        ("Reranker", "reranker_provider", "reranker_endpoint", "reranker_api_key"),
    ],
)
def test_a_family_with_its_own_key_is_untouched(label, provider_key, endpoint_key, api_key_key):
    overrides = {
        "llm_api_key": OTHER,
        provider_key: "production",
        endpoint_key: REMOTE,
        api_key_key: OTHER,
    }

    assert _settings(**overrides).model_dump()[provider_key] == "production"


def test_pa133_still_speaks_first_for_an_empty_key():
    """An empty key cannot carry the token; PA-133's message must not be masked."""

    with pytest.raises(ValueError, match="has no LLM_API_KEY") as excinfo:
        _settings(llm_api_key="")

    assert "only sent to a local endpoint" in str(excinfo.value)


def test_a_remote_endpoint_that_resolves_to_no_host_still_names_it():
    with pytest.raises(ValueError, match="<no host>"):
        _settings(llm_base_url="https:///compatible-mode/v1")


def test_the_guard_does_not_fire_when_the_token_is_merely_present_not_used():
    """A provider in development mode never gets a key, so it cannot leak one."""

    settings = _settings(
        llm_api_key=OTHER, vlm_provider="development", vlm_api_key=SHARED
    )

    assert settings.vlm_api_key.get_secret_value() == SHARED
