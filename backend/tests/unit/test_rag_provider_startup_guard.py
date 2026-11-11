"""t165: a production app must not answer RAG questions from a synthetic provider.

The RAG chain has four providers, and every "development" variant answers from
local arithmetic instead of a model: lexical overlap for the reranker, hashed
pseudo-vectors for embeddings, a canned string for the LLM. That is exactly what
a unit test wants, and precisely what production must never ship — citations and
relevance scores would look computed while no model ran, so every downstream
check (evidence excerpt, permission filtering, citation ids) passes on fabric.

`app.core.startup.verify_rag_providers_are_real` is the assembly-time guard. It
reads the settings (so a misconfigured deployment cannot even boot) and, when the
caller passes the built providers, also rejects the development classes and any
HTTP provider carrying an injected client — the seam through which a test mock
would reach production.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.core.startup import (
    rag_provider_problems,
    verify_rag_providers_are_real,
)
from app.provider.embedding import DevelopmentEmbeddingProvider
from app.provider.reranker import (
    DevelopmentRerankerProvider,
    HttpRerankerProvider,
)

REAL = {
    "embedding_provider": "production",
    "embedding_endpoint": "http://host.docker.internal:11436/v1/embeddings",
    "reranker_provider": "production",
    "reranker_endpoint": "http://host.docker.internal:11436/v1/rerank",
    "rag_llm_provider": "production",
    "llm_provider": "production",
}

# The guard reads four provider fields. A fully credential-complete production
# environment is a different subject (test_config.py owns that), so build the
# production shape by copying a valid baseline and flipping the fields the guard
# looks at. `model_copy` skips validators by design; that is exactly why the
# guard has to exist at assembly time as well.
_BASELINE = Settings(_env_file=None, app_env="test")
PRODUCTION = {
    "app_env": "production",
    "embedding_provider": "development",
    "embedding_endpoint": "",
    "reranker_provider": "development",
    "reranker_endpoint": "",
    "rag_llm_provider": "inherit",
    "llm_provider": "development",
}


def _settings(**overrides) -> Settings:
    return _BASELINE.model_copy(update={**PRODUCTION, **overrides})


def _http_reranker() -> HttpRerankerProvider:
    return HttpRerankerProvider(
        endpoint="http://127.0.0.1:11436/v1/rerank",
        timeout_seconds=15.0,
        model="bge-reranker-v2-m3",
    )


def test_production_with_real_providers_assembles() -> None:
    """四个 provider 都指向真实端点：不抛，且没有可报告的问题。"""

    settings = _settings(**REAL)
    assert rag_provider_problems(settings) == []
    verify_rag_providers_are_real(settings)


def test_production_with_development_providers_refuses_to_start() -> None:
    """生产环境用 development provider：装配期就拒绝，并把四个都点出来。"""

    settings = _settings()
    with patch("app.core.startup.logger.error") as error:
        with pytest.raises(RuntimeError) as excinfo:
            verify_rag_providers_are_real(settings)

    message = str(excinfo.value)
    assert "embedding_provider=development" in message
    assert "reranker_provider=development" in message
    # 默认 rag_llm_provider=inherit，而 llm_provider 也是 development
    assert "rag_llm_provider=inherit while llm_provider=development" in message
    assert error.call_args.args[0] == "rag_provider_not_real"
    assert error.call_args.kwargs["problems"] == rag_provider_problems(settings)


def test_production_requires_the_reranker_endpoint() -> None:
    """声明 production 却没给端点：配置期校验之外再兜一层装配期闸。"""

    settings = _settings(**{**REAL, "reranker_endpoint": "  "})
    assert rag_provider_problems(settings) == [
        "reranker_provider=production without an endpoint"
    ]
    with pytest.raises(RuntimeError, match="reranker_provider=production without an endpoint"):
        verify_rag_providers_are_real(settings)


def test_staging_and_test_environments_stay_on_the_development_providers() -> None:
    """非生产环境照旧：测试与预发需要本地算术 provider，护栏不能误伤。"""

    for app_env in ("test", "development", "staging"):
        settings = Settings(_env_file=None, app_env=app_env)
        assert rag_provider_problems(settings) == []
        verify_rag_providers_are_real(settings)


def test_built_development_provider_is_rejected_even_if_settings_look_real() -> None:
    """设置写的是 production，但装配拿到的是 development 实例：同一道闸拦下。"""

    settings = _settings(**REAL)
    with pytest.raises(RuntimeError, match="reranker=DevelopmentRerankerProvider is a synthetic provider"):
        verify_rag_providers_are_real(
            settings,
            providers=[
                ("embedding", DevelopmentEmbeddingProvider()),
                ("reranker", DevelopmentRerankerProvider()),
            ],
        )


def test_injected_client_is_treated_as_a_mock_transport() -> None:
    """HTTP provider 带着注入的 client（MockTransport 的入口）：生产不接受。"""

    documents = ["厨房水管漏水请联系物业", "停车位管理规定"]
    mock = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"scores": [1.0, 0.0]})
        )
    )
    provider = HttpRerankerProvider(
        endpoint="http://127.0.0.1:11436/v1/rerank",
        timeout_seconds=15.0,
        model="bge-reranker-v2-m3",
        client=mock,
    )
    # 该 client 确实会伪造分数——所以生产装配必须拒绝它
    assert provider.rerank(query="漏水", documents=documents) == (1.0, 0.0)

    with pytest.raises(RuntimeError, match="reranker=HttpRerankerProvider\\(injected client\\)"):
        verify_rag_providers_are_real(
            _settings(**REAL),
            providers=[("reranker", provider)],
        )


def test_a_real_http_provider_passes_the_object_check() -> None:
    """真 HTTP provider（无注入 client）：对象层校验放行。"""

    verify_rag_providers_are_real(
        _settings(**REAL),
        providers=[("reranker", _http_reranker())],
    )
