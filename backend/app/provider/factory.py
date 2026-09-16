"""Build Development providers from settings. Production SDKs stay out of services."""

from app.core.config import Settings
from app.provider.asr import ASRProvider, DevelopmentASRProvider
from app.provider.llm import DevelopmentLLMProvider, LLMProvider
from app.provider.vision import DevelopmentVLMProvider, VLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    return DevelopmentLLMProvider(settings.llm_provider_behavior)


def build_vlm_provider(settings: Settings) -> VLMProvider:
    return DevelopmentVLMProvider(settings.vlm_provider_behavior)


def build_asr_provider(settings: Settings) -> ASRProvider:
    return DevelopmentASRProvider(settings.asr_provider_behavior)
