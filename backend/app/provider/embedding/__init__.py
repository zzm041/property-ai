"""Embedding Provider. Fake is deterministic; production uses a configured HTTP adapter."""

from collections.abc import Sequence
import hashlib
import math
import re

from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError


class EmbeddingProvider:
    dimensions: int
    model_version: str

    def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
        raise NotImplementedError


class DevelopmentEmbeddingProvider(EmbeddingProvider):
    """Character/n-gram hashing. Similar Chinese questions land nearby."""

    model_version = "dev-bge-m3-fake"

    def __init__(self, behavior: str = "success", dimensions: int = 8) -> None:
        self.behavior = behavior
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
        if self.behavior == "timeout":
            raise ProviderTimeoutError("Embedding timed out")
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("Embedding is unavailable")
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> list[float]:
        tokens = _tokens(text)
        values = [0.0] * self.dimensions
        if not tokens:
            values[0] = 1.0
            return values
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            values[index] += 1.0
        norm = math.sqrt(sum(item * item for item in values)) or 1.0
        return [item / norm for item in values]


class HttpEmbeddingProvider(EmbeddingProvider):
    """Production adapter. The FastAPI process does not load BGE-M3 weights."""

    model_version = "bge-m3"

    def __init__(self, *, endpoint: str, timeout_seconds: float, dimensions: int) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
        del texts
        raise ProviderUnavailableError("Production embedding endpoint is not configured")


def _tokens(text: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", text.casefold())
    if not compact:
        return ()
    grams = [compact[index : index + 2] for index in range(len(compact) - 1)]
    return tuple([*compact, *grams][:256])
