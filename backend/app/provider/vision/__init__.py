"""VLM / Vision Provider. Results are suggestions only."""

from dataclasses import dataclass
from typing import Protocol

from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError


@dataclass(frozen=True, slots=True)
class VisionInsight:
    description: str
    labels: tuple[str, ...]
    confidence: float
    model_version: str


class VLMProvider(Protocol):
    model_version: str

    def inspect_image(self, *, storage_key: str, mime_type: str) -> VisionInsight:
        """Describe a stored image. Must not decide dispatch or closure."""


VisionProvider = VLMProvider


class DevelopmentVLMProvider:
    model_version = "dev-vlm-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def inspect_image(self, *, storage_key: str, mime_type: str) -> VisionInsight:
        del storage_key, mime_type
        if self.behavior == "timeout":
            raise ProviderTimeoutError("VLM timed out")
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("VLM is unavailable")
        return VisionInsight(
            description="Scorched outlet cover with visible spark marks",
            labels=("electrical", "burn_mark"),
            confidence=0.74,
            model_version=self.model_version,
        )
