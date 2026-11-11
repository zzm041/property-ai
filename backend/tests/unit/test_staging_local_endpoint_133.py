"""PA-133: a staging box that leans on the local token must point at a local endpoint.

`LOCAL_AI_INTERNAL_TOKEN` is the adapter's shared secret on this host. `resolve_local_ai_key`
deliberately withholds it for any other hostname, so "token + remote endpoint" used to
validate cleanly and then send an empty bearer at the first request. These tests pin the
startup side: the only accepted shape is token + local endpoint.

The runtime semantics of `resolve_local_ai_key` are untouched - see
`test_day2_providers.py::test_local_ai_token_fallback_is_limited_to_local_endpoints`.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import LOCAL_AI_ENDPOINT_HOSTS, Settings

LOCAL_TOKEN = "local-internal-token-43-chars-long-xxxxxxxxxxx"
LOCAL_ENDPOINT = "http://host.docker.internal:11437/v1"
REMOTE_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _staging(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "staging",
        "llm_provider": "production",
        "database_url": "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:11437/v1",
        "http://localhost:11437/v1",
        "http://host.docker.internal:11437/v1",
    ],
)
def test_token_with_a_local_endpoint_starts(endpoint: str) -> None:
    credentials = _staging(local_ai_internal_token=LOCAL_TOKEN, llm_base_url=endpoint).ai_credentials()

    assert credentials["status"] == "configured"
    assert credentials["missing"] == []
    assert credentials["sources"]["resolved_from"] == "local_ai_internal_token"


def test_host_docker_internal_is_the_container_alias_for_this_host() -> None:
    """The delivery stack reaches the host adapter as host.docker.internal, not 127.0.0.1."""

    assert "host.docker.internal" in LOCAL_AI_ENDPOINT_HOSTS

    credentials = _staging(
        local_ai_internal_token=LOCAL_TOKEN, llm_base_url=LOCAL_ENDPOINT
    ).ai_credentials()

    assert credentials["endpoint"] == LOCAL_ENDPOINT
    assert credentials["fallback_to_local_token"] is True


def test_token_with_a_remote_endpoint_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="only sent to a local endpoint") as excinfo:
        _staging(local_ai_internal_token=LOCAL_TOKEN, llm_base_url=REMOTE_ENDPOINT)

    message = str(excinfo.value)
    # the operator has to be able to fix it from the message alone
    assert "LOCAL_AI_INTERNAL_TOKEN" in message
    assert "dashscope.aliyuncs.com" in message
    assert "127.0.0.1" in message and "host.docker.internal" in message


def test_a_hostname_that_only_looks_local_is_refused() -> None:
    """A prefix match would let `localhost.attacker.example` through; an exact set does not."""

    with pytest.raises(ValueError, match="localhost.attacker.example"):
        _staging(
            local_ai_internal_token=LOCAL_TOKEN,
            llm_base_url="https://localhost.attacker.example/v1",
        )


def test_an_explicit_key_is_still_allowed_to_be_remote() -> None:
    """The gate only fires on the fallback path - a real key may point anywhere."""

    credentials = _staging(
        llm_api_key="sk-present", llm_base_url=REMOTE_ENDPOINT
    ).ai_credentials()

    assert credentials["status"] == "configured"
    assert credentials["sources"]["resolved_from"] == "llm_api_key"


def test_an_endpoint_without_a_hostname_names_what_is_wrong() -> None:
    with pytest.raises(ValueError, match=r"points at <no host>"):
        _staging(local_ai_internal_token=LOCAL_TOKEN, llm_base_url="://definitely-not-a-url")


def test_the_gate_and_the_runtime_guard_share_one_whitelist() -> None:
    """If these two ever drift, a box validates and then sends an empty bearer."""

    settings = _staging(llm_api_key="sk-present", llm_base_url=REMOTE_ENDPOINT)
    settings = settings.model_copy(
        update={
            "llm_api_key": SecretStr(""),
            "local_ai_internal_token": SecretStr(LOCAL_TOKEN),
        }
    )
    for host in LOCAL_AI_ENDPOINT_HOSTS:
        assert settings.resolve_local_ai_key(SecretStr(""), f"http://{host}:11437/v1") == LOCAL_TOKEN
    assert settings.resolve_local_ai_key(SecretStr(""), REMOTE_ENDPOINT) == ""
