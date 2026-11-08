"""Real HTTP adapter validation using MockTransport, never an external endpoint."""

import httpx
import pytest

from app.provider.errors import ProviderInvalidOutputError
from app.provider.reranker import HttpRerankerProvider


@pytest.mark.parametrize("payload", [
    [], None, {"scores": []}, {"scores": [0.1, 0.2]},
    {"scores": [True]}, {"scores": [None]}, {"scores": ["nan"]},
    {"scores": ["inf"]}, {"scores": ["-inf"]}, {"scores": ["not-a-number"]},
    {"results": [{"score": True}]},
])
def test_http_reranker_rejects_invalid_shape_and_nonfinite_scores(payload):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=payload),
    )) as client:
        provider = HttpRerankerProvider(endpoint="https://synthetic.invalid/rerank", timeout_seconds=1, client=client)
        with pytest.raises(ProviderInvalidOutputError):
            provider.rerank(query="synthetic", documents=("synthetic passage",))


@pytest.mark.parametrize("payload,expected", [
    ({"scores": [0]}, (0.0,)),
    ({"scores": ["0.8"]}, (0.8,)),
    ({"results": [{"relevance_score": 0.7}]}, (0.7,)),
    ({"scores": [{"score": -0.2}]}, (-0.2,)),
])
def test_http_reranker_preserves_finite_supported_formats(payload, expected):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json=payload),
    )) as client:
        provider = HttpRerankerProvider(endpoint="https://synthetic.invalid/rerank", timeout_seconds=1, client=client)
        assert provider.rerank(query="synthetic", documents=("synthetic passage",)) == expected
