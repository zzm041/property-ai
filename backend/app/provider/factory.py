"""Build Development and production-adapter providers from settings."""

from app.core.config import Settings
from app.provider.asr import ASRProvider, DevelopmentASRProvider, HttpASRProvider
from app.provider.embedding import (
    DevelopmentEmbeddingProvider,
    EmbeddingProvider,
    HttpEmbeddingProvider,
)
from app.provider.llm import DevelopmentLLMProvider, LLMProvider, QwenLLMProvider
from app.provider.notification import (
    DevelopmentNotificationProvider,
    NotificationProvider,
)
from app.provider.reranker import (
    DevelopmentRerankerProvider,
    HttpRerankerProvider,
    RerankerProvider,
)
from app.provider.storage import (
    DevelopmentStorageProvider,
    ProductionStorageProvider,
    StorageProvider,
)
from app.provider.vector import MilvusKnowledgeStore
from app.provider.vision import DevelopmentVLMProvider, QwenVLMProvider, VLMProvider
from app.provider.sms import SmsProvider, development_sms_inbox
from app.provider.wechat import (
    DevelopmentWeChatAuthProvider,
    ProductionWeChatAuthProvider,
    WeChatAuthProvider,
)


def build_storage_provider(settings: Settings) -> StorageProvider:
    if settings.storage_provider == "production":
        return ProductionStorageProvider(
            endpoint=settings.storage_endpoint,
            bucket=settings.storage_bucket,
            access_key=settings.storage_access_key.get_secret_value(),
            secret_key=settings.storage_secret_key.get_secret_value(),
            region=settings.storage_region,
            presign_ttl_seconds=settings.storage_presign_ttl_seconds,
        )
    return DevelopmentStorageProvider(persist_dir=settings.dev_storage_dir or None)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "production":
        return QwenLLMProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return DevelopmentLLMProvider(settings.llm_provider_behavior)


def build_vlm_provider(
    settings: Settings,
    storage: StorageProvider | None = None,
) -> VLMProvider:
    if settings.vlm_provider == "production":
        return QwenVLMProvider(
            base_url=settings.vlm_base_url,
            model=settings.vlm_model,
            api_key=settings.vlm_api_key.get_secret_value(),
            timeout_seconds=settings.vlm_timeout_seconds,
            storage=storage or build_storage_provider(settings),
        )
    return DevelopmentVLMProvider(settings.vlm_provider_behavior)


def build_asr_provider(
    settings: Settings,
    storage: StorageProvider | None = None,
) -> ASRProvider:
    if settings.asr_provider == "production":
        return HttpASRProvider(
            base_url=settings.asr_base_url,
            model=settings.asr_model,
            api_key=settings.asr_api_key.get_secret_value(),
            timeout_seconds=settings.asr_timeout_seconds,
            storage=storage or build_storage_provider(settings),
        )
    return DevelopmentASRProvider(settings.asr_provider_behavior)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "production":
        return HttpEmbeddingProvider(
            endpoint=settings.embedding_endpoint,
            timeout_seconds=settings.embedding_timeout_seconds,
            dimensions=settings.embedding_dimensions,
            api_key=settings.embedding_api_key.get_secret_value(),
            model=settings.embedding_model,
        )
    return DevelopmentEmbeddingProvider(
        settings.embedding_provider_behavior,
        dimensions=settings.embedding_dimensions,
    )


def build_reranker_provider(settings: Settings) -> RerankerProvider:
    if settings.reranker_provider == "production":
        return HttpRerankerProvider(
            endpoint=settings.reranker_endpoint,
            timeout_seconds=settings.reranker_timeout_seconds,
            api_key=settings.reranker_api_key.get_secret_value(),
            model=settings.reranker_model,
        )
    return DevelopmentRerankerProvider(settings.reranker_provider_behavior)


def build_sms_provider(settings: Settings) -> SmsProvider:
    if settings.sms_provider == "development":
        return development_sms_inbox()
    raise RuntimeError("Production SMS provider is not implemented")


def build_wechat_auth_provider(settings: Settings) -> WeChatAuthProvider:
    if settings.wechat_provider == "production":
        return ProductionWeChatAuthProvider(
            appid=settings.wechat_app_id,
            secret=settings.wechat_app_secret.get_secret_value(),
            timeout_seconds=settings.wechat_timeout_seconds,
        )
    return DevelopmentWeChatAuthProvider(appid=settings.wechat_app_id)


def build_notification_provider(settings: Settings) -> NotificationProvider:
    return DevelopmentNotificationProvider(settings.notification_provider_behavior)


def build_milvus_store(settings: Settings) -> MilvusKnowledgeStore:
    return MilvusKnowledgeStore(settings)
