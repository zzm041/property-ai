"""Reranker Provider. Fake uses lexical overlap; production is HTTP-configured."""

from collections.abc import Mapping, Sequence
from math import isfinite

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

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        api_key: str = "",
        model: str = "bge-reranker",
        client=None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key
        self.model = model
        self._client = client
        self.model_version = model or self.model_version

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        if not self.endpoint:
            raise ProviderUnavailableError("Reranker is unavailable")
        from app.provider.errors import ProviderInvalidOutputError
        from app.provider.http import request_json

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = request_json(
            method="POST",
            url=self.endpoint,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            json_body={
                "model": self.model,
                "query": query,
                "documents": list(documents),
            },
            client=self._client,
            provider="reranker",
            operation="rerank",
        )
        if not isinstance(payload, Mapping):
            raise ProviderInvalidOutputError("Reranker returned invalid JSON")
        scores = payload.get("scores")
        if scores is None:
            scores = payload.get("results")
        if not isinstance(scores, list) or len(scores) != len(documents):
            raise ProviderInvalidOutputError("Reranker returned invalid JSON")
        values: list[float] = []
        for item in scores:
            if isinstance(item, dict):
                item = item.get("score", item.get("relevance_score"))
            if isinstance(item, bool):
                raise ProviderInvalidOutputError("Reranker returned invalid JSON")
            try:
                value = float(item)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProviderInvalidOutputError("Reranker returned invalid JSON") from exc
            if not isfinite(value):
                raise ProviderInvalidOutputError("Reranker returned invalid JSON")
            values.append(value)
        return tuple(values)
