"""PA-122: staging now gates on the endpoint too, and the worker probe sees it."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.worker import healthcheck

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LOCAL_ENDPOINT = "http://host.docker.internal:11437/v1"
LOCAL_TOKEN = "local-internal-token-43-chars-long-xxxxxxxxxxx"


def _staging(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "staging",
        "llm_provider": "production",
        "database_url": "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
    }
    values.update(overrides)
    return Settings(**values)


def test_staging_rejects_a_production_provider_without_an_endpoint() -> None:
    """PA-122 主症状：有 key、无端点，修前 rc=0 然后 0 ms 抛 ProviderUnavailableError。"""

    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        _staging(llm_api_key="sk-present", llm_base_url="")


def test_staging_accepts_an_explicit_key_with_an_endpoint() -> None:
    credentials = _staging(
        llm_api_key="sk-present", llm_base_url=ENDPOINT
    ).ai_credentials()

    assert credentials["status"] == "configured"
    assert credentials["missing"] == []
    assert credentials["endpoint"] == ENDPOINT
    assert credentials["sources"]["resolved_from"] == "llm_api_key"


def test_staging_accepts_the_local_token_with_a_local_endpoint() -> None:
    credentials = _staging(
        local_ai_internal_token=LOCAL_TOKEN, llm_base_url=LOCAL_ENDPOINT
    ).ai_credentials()

    assert credentials["status"] == "configured"
    assert credentials["missing"] == []
    assert credentials["sources"]["resolved_from"] == "local_ai_internal_token"
    assert credentials["fallback_to_local_token"] is True


def test_staging_still_rejects_a_box_with_no_credential_at_all() -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY or LOCAL_AI_INTERNAL_TOKEN"):
        _staging(llm_base_url=ENDPOINT)


def test_readyz_reports_the_endpoint_as_a_missing_ingredient() -> None:
    """校验器之外构造出来的对象（老配置、测试桩）也必须被 /readyz 说清。"""

    sneaky = _staging(llm_api_key="sk-present", llm_base_url=ENDPOINT).model_copy(
        update={"llm_base_url": ""}
    )

    credentials = sneaky.ai_credentials()

    assert credentials["status"] == "missing"
    assert credentials["missing"] == ["LLM_BASE_URL"]
    assert credentials["endpoint"] == ""


def test_worker_probe_fails_when_the_credential_is_unusable(monkeypatch) -> None:
    """worker 健康探针不能只 ping：凭证不可用的 worker 会把每份草稿都跑挂。"""

    class _Pong:
        def ping(self):
            return {"celery@probe": {"ok": "pong"}}

    monkeypatch.setattr(
        healthcheck.celery_app.control, "inspect", lambda timeout=None: _Pong()
    )
    sneaky = _staging(llm_api_key="sk-present", llm_base_url=ENDPOINT).model_copy(
        update={"llm_base_url": ""}
    )

    healthy, detail = healthcheck._probe_worker(sneaky)
    assert healthy is False
    assert "LLM_BASE_URL" in detail

    healthy_ok, detail_ok = healthcheck._probe_worker(
        _staging(llm_api_key="sk-present", llm_base_url=ENDPOINT)
    )
    assert healthy_ok is True
    assert "ai_credentials=configured" in detail_ok
