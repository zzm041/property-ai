"""Day 2 production provider adapters. Secrets never appear in parsed output."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.provider.asr import HttpASRProvider
from app.provider.embedding import HttpEmbeddingProvider
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.provider.factory import (
    build_file_scanner_provider,
    build_asr_provider,
    build_embedding_provider,
    build_llm_provider,
    build_rag_llm_provider,
    build_reranker_provider,
    build_storage_provider,
    build_vlm_provider,
    build_wechat_auth_provider,
)
from app.provider.file_scanner import (
    DevelopmentFileScannerProvider,
    UnavailableFileScannerProvider,
)
from app.provider.llm import DevelopmentLLMProvider, QwenLLMProvider
from app.provider.reranker import HttpRerankerProvider
from app.provider.storage import DevelopmentStorageProvider, ProductionStorageProvider
from app.provider.vision import DevelopmentVLMProvider, QwenVLMProvider
from app.provider.wechat import (
    DevelopmentWeChatAuthProvider,
    ProductionWeChatAuthProvider,
)
from app.provider.asr import DevelopmentASRProvider
from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.observed_event import work_order_analysis_response_format


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "app_env": "test"}
    values.update(overrides)
    return Settings(**values)


def test_factory_keeps_development_fakes_by_default() -> None:
    settings = _settings()
    assert isinstance(build_llm_provider(settings), DevelopmentLLMProvider)
    assert isinstance(build_rag_llm_provider(settings), DevelopmentLLMProvider)
    assert isinstance(build_vlm_provider(settings), DevelopmentVLMProvider)
    assert isinstance(build_asr_provider(settings), DevelopmentASRProvider)
    assert isinstance(build_wechat_auth_provider(settings), DevelopmentWeChatAuthProvider)
    assert isinstance(build_storage_provider(settings), DevelopmentStorageProvider)
    assert isinstance(
        build_file_scanner_provider(settings), UnavailableFileScannerProvider
    )


def test_development_file_scanner_is_explicit_and_rejected_in_production() -> None:
    settings = _settings(file_scanner_provider="development")
    assert isinstance(
        build_file_scanner_provider(settings), DevelopmentFileScannerProvider
    )

    with pytest.raises(ValueError, match="Development file scanner"):
        _settings(
            app_env="production",
            app_secret_key="delivery-production-secret-not-a-default",
            wechat_login_enabled=False,
            file_scanner_provider="development",
        )


def test_qwen_llm_parses_structured_json_and_strips_fences() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-live"
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_object"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"category":"electrical","confidence":0.9}\n```'
                        }
                    }
                ]
            },
        )

    provider = QwenLLMProvider(
        base_url="https://llm.example/v1",
        model="qwen-plus",
        api_key="sk-live",
        timeout_seconds=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.structured_completion(prompt="outlet", prompt_version="wo-v1")
    assert result["category"] == "electrical"
    assert "sk-live" not in str(result)


def test_work_order_qwen_uses_canonical_json_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == work_order_analysis_response_format()
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["category"]["enum"] == [
            "water_supply", "electrical", "hvac", "cleaning", "security", "other"
        ]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"category":"other"}'}}]})

    provider = QwenLLMProvider(
        base_url="http://127.0.0.1:11436/v1",
        model="qwen3.5:4b",
        api_key="unit",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        disable_thinking=True,
        max_output_tokens=512,
    )
    provider.structured_completion(prompt="synthetic", prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION)


def test_qwen_llm_maps_timeout_and_invalid_payload() -> None:
    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    timed_out = QwenLLMProvider(
        base_url="https://llm.example/v1",
        model="qwen-plus",
        api_key="sk-live",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
    )
    with pytest.raises(ProviderTimeoutError):
        timed_out.structured_completion(prompt="x", prompt_version="wo-v1")

    def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    invalid = QwenLLMProvider(
        base_url="https://llm.example/v1",
        model="qwen-plus",
        api_key="sk-live",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(invalid_handler)),
    )
    with pytest.raises(ProviderInvalidOutputError):
        invalid.structured_completion(prompt="x", prompt_version="wo-v1")


def test_qwen_vlm_reads_only_storage_object() -> None:
    storage = DevelopmentStorageProvider()
    stored = storage.register_object(
        mime_type="image/jpeg",
        size_bytes=4,
        tenant_id=1,
        property_id=2,
    )
    storage.put_object(stored.storage_key, b"jpeg", "image/jpeg")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        assert any(item.get("type") == "image_url" for item in content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"description":"burn mark","labels":["electrical"],"confidence":0.7}'
                        }
                    }
                ]
            },
        )

    provider = QwenVLMProvider(
        base_url="https://vlm.example/v1",
        model="qwen-vl-plus",
        api_key="sk-vlm",
        timeout_seconds=2,
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    insight = provider.inspect_image(storage_key=stored.storage_key, mime_type="image/jpeg")
    assert insight.description == "burn mark"
    assert insight.labels == ("electrical",)


def test_http_asr_returns_text_without_secret() -> None:
    storage = DevelopmentStorageProvider()
    stored = storage.register_object(mime_type="audio/mpeg", size_bytes=3)
    storage.put_object(stored.storage_key, b"mp3", "audio/mpeg")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "sk-asr" not in request.content.decode("latin1", errors="ignore")
        return httpx.Response(200, json={"text": "kitchen outlet sparking", "confidence": 0.8})

    provider = HttpASRProvider(
        base_url="https://asr.example/v1",
        model="paraformer",
        api_key="sk-asr",
        timeout_seconds=2,
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transcript = provider.transcribe(storage_key=stored.storage_key, mime_type="audio/mpeg")
    assert transcript.text == "kitchen outlet sparking"
    assert "sk-asr" not in transcript.text


def test_http_embedding_validates_batch_dimension_and_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        vectors = [[0.1, 0.2, 0.3, 0.4] for _ in payload["input"]]
        return httpx.Response(200, json={"data": [{"embedding": item} for item in vectors]})

    provider = HttpEmbeddingProvider(
        endpoint="https://emb.example/v1",
        timeout_seconds=2,
        dimensions=4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    batch = provider.embed(("one", "two"))
    assert len(batch) == 2
    assert len(batch[0]) == 4
    with pytest.raises(ProviderInvalidOutputError):
        provider.embed(())

    mismatch = HttpEmbeddingProvider(
        endpoint="https://emb.example/v1",
        timeout_seconds=2,
        dimensions=8,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderInvalidOutputError, match="dimension"):
        mismatch.embed(("one",))


def test_http_reranker_scores_and_invalid_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(200, json={"scores": [0.9] * len(payload["documents"])})

    provider = HttpRerankerProvider(
        endpoint="https://rerank.example/score",
        timeout_seconds=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    scores = provider.rerank(query="装修", documents=("a", "b"))
    assert scores == (0.9, 0.9)

    def bad(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"scores": [0.1]})

    broken = HttpRerankerProvider(
        endpoint="https://rerank.example/score",
        timeout_seconds=2,
        client=httpx.Client(transport=httpx.MockTransport(bad)),
    )
    with pytest.raises(ProviderInvalidOutputError):
        broken.rerank(query="装修", documents=("a", "b"))


def test_http_provider_maps_429_and_500() -> None:
    def limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    provider = QwenLLMProvider(
        base_url="https://llm.example/v1",
        model="qwen-plus",
        api_key="sk-live",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(limited)),
    )
    with pytest.raises(ProviderRateLimitError):
        provider.structured_completion(prompt="x", prompt_version="wo-v1")

    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    unavailable = QwenLLMProvider(
        base_url="https://llm.example/v1",
        model="qwen-plus",
        api_key="sk-live",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(ProviderUnavailableError):
        unavailable.structured_completion(prompt="x", prompt_version="wo-v1")


def test_production_storage_opaque_key_and_short_presign() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, content=b"stored-bytes")

    provider = ProductionStorageProvider(
        endpoint="http://minio:9000",
        bucket="property-ai",
        access_key="AKIAEXAMPLE",
        secret_key="super-secret-key",
        presign_ttl_seconds=300,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    stored = provider.register_object(
        mime_type="image/jpeg",
        size_bytes=12,
        tenant_id=9,
        property_id=8,
    )
    assert stored.storage_key.startswith("t9/p8/")
    assert stored.upload_url is not None
    assert "super-secret-key" not in stored.upload_url
    assert stored.upload_expires_in == 300
    assert "X-Amz-Expires=300" in stored.upload_url
    provider.put_object(stored.storage_key, b"stored-bytes", "image/jpeg")
    assert provider.get_object(stored.storage_key) == b"stored-bytes"


def test_wechat_production_parser_drops_session_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "secret" in str(request.url)
        return httpx.Response(
            200,
            json={
                "openid": "o-real",
                "session_key": "must-not-leak",
                "unionid": "u-real",
            },
        )

    provider = ProductionWeChatAuthProvider(
        appid="wx123",
        secret="app-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    session = provider.exchange_code("wxcode")
    assert session.openid == "o-real"
    assert session.unionid == "u-real"
    dumped = f"{session.openid} {session.unionid} {session.appid}"
    assert "must-not-leak" not in dumped
    assert "app-secret" not in dumped


def test_wechat_invalid_and_unavailable_errcodes() -> None:
    def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})

    provider = ProductionWeChatAuthProvider(
        appid="wx123",
        secret="app-secret",
        client=httpx.Client(transport=httpx.MockTransport(invalid)),
    )
    with pytest.raises(ProviderInvalidOutputError):
        provider.exchange_code("bad")

    def busy(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 45011})

    busy_provider = ProductionWeChatAuthProvider(
        appid="wx123",
        secret="app-secret",
        client=httpx.Client(transport=httpx.MockTransport(busy)),
    )
    with pytest.raises(ProviderUnavailableError):
        busy_provider.exchange_code("busy")


def test_factory_production_branches() -> None:
    settings = _settings(
        llm_provider="production",
        llm_base_url="https://llm.example/v1",
        llm_api_key="sk",
        vlm_provider="production",
        vlm_base_url="https://vlm.example/v1",
        vlm_api_key="sk",
        asr_provider="production",
        asr_base_url="https://asr.example/v1",
        asr_api_key="sk",
        embedding_provider="production",
        embedding_endpoint="https://emb.example/v1",
        reranker_provider="production",
        reranker_endpoint="https://rerank.example",
        storage_provider="production",
        storage_endpoint="http://minio:9000",
        storage_bucket="property-ai",
        storage_access_key="ak",
        storage_secret_key="sk",
        wechat_provider="production",
        wechat_app_id="wx123",
        wechat_app_secret="secret",
    )
    assert isinstance(build_llm_provider(settings), QwenLLMProvider)
    assert isinstance(build_rag_llm_provider(settings), QwenLLMProvider)
    assert isinstance(build_vlm_provider(settings), QwenVLMProvider)
    assert isinstance(build_asr_provider(settings), HttpASRProvider)
    assert isinstance(build_embedding_provider(settings), HttpEmbeddingProvider)
    assert isinstance(build_reranker_provider(settings), HttpRerankerProvider)
    assert isinstance(build_storage_provider(settings), ProductionStorageProvider)
    assert isinstance(build_wechat_auth_provider(settings), ProductionWeChatAuthProvider)


def test_rag_llm_can_be_production_while_work_order_llm_stays_development() -> None:
    settings = _settings(
        llm_provider="development",
        rag_llm_provider="production",
        rag_llm_base_url="http://127.0.0.1:11436/v1",
        rag_llm_model="qwen3.5:4b",
        rag_llm_api_key="local-test-token",
        rag_llm_disable_thinking=True,
        rag_llm_max_output_tokens=512,
    )

    assert isinstance(build_llm_provider(settings), DevelopmentLLMProvider)
    rag = build_rag_llm_provider(settings)
    assert isinstance(rag, QwenLLMProvider)
    assert rag.model_version == "qwen3.5:4b"


def test_local_ai_token_fallback_is_limited_to_local_endpoints() -> None:
    local = _settings(
        local_ai_internal_token="local-only-token",
        rag_llm_provider="production",
        rag_llm_base_url="http://host.docker.internal:11436/v1",
        embedding_provider="production",
        embedding_endpoint="http://host.docker.internal:11436/v1/embeddings",
        reranker_provider="production",
        reranker_endpoint="http://host.docker.internal:11436/v1/rerank",
    )
    assert build_rag_llm_provider(local)._api_key == "local-only-token"
    assert build_embedding_provider(local)._api_key == "local-only-token"
    assert build_reranker_provider(local)._api_key == "local-only-token"

    external = _settings(
        local_ai_internal_token="must-not-leak",
        rag_llm_provider="production",
        rag_llm_base_url="https://models.example/v1",
        rag_llm_api_key="independent-rag-key",
        embedding_provider="production",
        embedding_endpoint="https://models.example/v1/embeddings",
        embedding_api_key="independent-embedding-key",
        reranker_provider="production",
        reranker_endpoint="https://models.example/v1/rerank",
        reranker_api_key="independent-reranker-key",
    )
    assert build_rag_llm_provider(external)._api_key == "independent-rag-key"
    assert build_embedding_provider(external)._api_key == "independent-embedding-key"
    assert build_reranker_provider(external)._api_key == "independent-reranker-key"


def test_production_rag_llm_requires_its_own_endpoint_and_secret() -> None:
    with pytest.raises(ValueError, match="Production RAG LLM"):
        Settings(
            _env_file=None,
            app_env="production",
            app_secret_key="production-secret-that-is-long-enough",
            wechat_login_enabled=False,
            rag_llm_provider="production",
        )


def test_development_storage_persists_across_instances(tmp_path) -> None:
    from app.provider import storage as storage_mod

    directory = tmp_path / "objects"
    first = DevelopmentStorageProvider(persist_dir=str(directory))
    stored = first.register_object(
        mime_type="image/jpeg",
        size_bytes=4,
        tenant_id=1,
        property_id=2,
    )
    first.put_object(stored.storage_key, b"jpeg", "image/jpeg")
    storage_mod._DEV_OBJECTS.pop(stored.storage_key, None)
    second = DevelopmentStorageProvider(persist_dir=str(directory))
    assert second.get_object(stored.storage_key) == b"jpeg"
    assert stored.storage_key.startswith("dev-object:")
    assert "jpeg" not in stored.storage_key
