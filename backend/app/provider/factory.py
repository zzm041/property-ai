"""Build Development and production-adapter providers from settings."""

from app.core.config import Settings
from app.provider.asr import ASRProvider, DevelopmentASRProvider
from app.provider.embedding import (
    DevelopmentEmbeddingProvider,
    EmbeddingProvider,
    HttpEmbeddingProvider,
)
from app.provider.llm import DevelopmentLLMProvider, LLMProvider
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
from app.provider.vision import DevelopmentVLMProvider, VLMProvider
from app.provider.wechat import (
    DevelopmentWeChatAuthProvider,
    ProductionWeChatAuthProvider,
    WeChatAuthProvider,
)


def build_llm_provider(settings: Settings) -> LLMProvider:
    return DevelopmentLLMProvider(settings.llm_provider_behavior)


def build_vlm_provider(settings: Settings) -> VLMProvider:
    return DevelopmentVLMProvider(settings.vlm_provider_behavior)


def build_asr_provider(settings: Settings) -> ASRProvider:
    return DevelopmentASRProvider(settings.asr_provider_behavior)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "production":
        return HttpEmbeddingProvider(
            endpoint=settings.embedding_endpoint,
            timeout_seconds=settings.ai_llm_timeout_seconds,
            dimensions=settings.embedding_dimensions,
        )
    return DevelopmentEmbeddingProvider(
        settings.embedding_provider_behavior,
        dimensions=settings.embedding_dimensions,
    )


def build_reranker_provider(settings: Settings) -> RerankerProvider:
    if settings.reranker_provider == "production":
        return HttpRerankerProvider(
            endpoint=settings.reranker_endpoint,
            timeout_seconds=settings.ai_llm_timeout_seconds,
        )
    return DevelopmentRerankerProvider(settings.reranker_provider_behavior)


def build_wechat_auth_provider(settings: Settings) -> WeChatAuthProvider:
    if settings.wechat_provider == "production":
        return ProductionWeChatAuthProvider(
            appid=settings.wechat_app_id,
            secret=settings.wechat_app_secret.get_secret_value(),
        )
    return DevelopmentWeChatAuthProvider(appid=settings.wechat_app_id)


def build_notification_provider(settings: Settings) -> NotificationProvider:
    return DevelopmentNotificationProvider(settings.notification_provider_behavior)


def build_storage_provider(settings: Settings) -> StorageProvider:
    if settings.storage_provider == "production":
        return ProductionStorageProvider(
            endpoint=settings.storage_endpoint,
            bucket=settings.storage_bucket,
        )
    return DevelopmentStorageProvider()


def build_milvus_store(settings: Settings) -> MilvusKnowledgeStore:
    return MilvusKnowledgeStore(settings)
