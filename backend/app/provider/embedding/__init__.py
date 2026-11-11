"""Embedding Provider. Fake is deterministic; production uses a configured HTTP adapter."""

from collections.abc import Sequence
import hashlib
import math
import re

from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


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

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        dimensions: int,
        api_key: str = "",
        model: str = "bge-m3",
        client=None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.dimensions = dimensions
        self._api_key = api_key
        self.model = model
        self._client = client
        self.model_version = model or self.model_version

    def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
        if not self.endpoint:
            raise ProviderUnavailableError("Embedding is unavailable")
        if not texts:
            raise ProviderInvalidOutputError("Embedding input is empty")
        from app.provider.http import request_json

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = request_json(
            method="POST",
            url=self.endpoint
            if self.endpoint.endswith("/embeddings")
            else f"{self.endpoint}/embeddings",
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            json_body={"model": self.model, "input": list(texts)},
            client=self._client,
            provider="embedding",
            operation="embed",
        )
        vectors = _parse_embeddings(payload)
        if len(vectors) != len(texts):
            raise ProviderInvalidOutputError("Embedding batch size mismatch")
        normalized: list[list[float]] = []
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ProviderInvalidOutputError("Embedding dimension mismatch")
            normalized.append([float(item) for item in vector])
        return tuple(normalized)


def _parse_embeddings(payload: object) -> tuple[list[float], ...]:
    from app.provider.errors import ProviderInvalidOutputError

    if not isinstance(payload, dict):
        raise ProviderInvalidOutputError("Embedding returned invalid JSON")
    if "data" in payload and isinstance(payload["data"], list):
        rows = []
        for item in payload["data"]:
            if not isinstance(item, dict) or "embedding" not in item:
                raise ProviderInvalidOutputError("Embedding returned invalid JSON")
            rows.append(list(item["embedding"]))
        return tuple(rows)
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        return tuple(list(item) for item in embeddings)
    raise ProviderInvalidOutputError("Embedding returned an empty response")


def _tokens(text: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", text.casefold())
    if not compact:
        return ()
    grams = [compact[index : index + 2] for index in range(len(compact) - 1)]
    return tuple([*compact, *grams][:256])
