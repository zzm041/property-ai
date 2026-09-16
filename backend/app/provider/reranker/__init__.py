"""Reranker Provider. Fake uses lexical overlap; production is HTTP-configured."""

from collections.abc import Sequence

from app.provider.errors import ProviderUnavailableError


class RerankerProvider:
    model_version: str

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        raise NotImplementedError


class DevelopmentRerankerProvider(RerankerProvider):
    model_version = "dev-reranker-v1"

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        if self.behavior in {"unavailable", "failure"}:
            raise ProviderUnavailableError("Reranker is unavailable")
        query_chars = set(query)
        scores: list[float] = []
        for document in documents:
            if not document:
                scores.append(0.0)
                continue
            overlap = len(query_chars.intersection(document))
            scores.append(overlap / max(len(query_chars), 1))
        return tuple(scores)


class HttpRerankerProvider(RerankerProvider):
    model_version = "bge-reranker"

    def __init__(self, *, endpoint: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        del query, documents
        raise ProviderUnavailableError("Production reranker endpoint is not configured")
