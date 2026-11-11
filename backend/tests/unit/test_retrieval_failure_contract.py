"""Query-level contracts with in-memory repositories and provider doubles only."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.domain.knowledge import INSUFFICIENT_KNOWLEDGE, KNOWLEDGE_UNAVAILABLE
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.service import retrieval


ERRORS = (
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderRateLimitError,
    ProviderInvalidOutputError,
)


@pytest.fixture
def query_context(monkeypatch, request):
    chunks = {
        key: SimpleNamespace(
            id=key, document_id=key + 20, version=1, tenant_id=7,
            property_id=9, visibility="RESIDENT", excerpt=f"synthetic passage {key}",
            source_locator=f"paragraph:{key}",
        )
        for key in (11, 12)
    }
    documents = {
        key + 20: SimpleNamespace(
            id=key + 20, title=f"document {key}", status="ACTIVE",
            current_version=1, visibility="RESIDENT", tenant_id=7, property_id=9,
        )
        for key in chunks
    }
    session = Session(expire_on_commit=False)
    request.addfinalizer(session.close)
    monkeypatch.setattr(session, "get", lambda _model, key: documents.get(key))
    actor = SimpleNamespace(user_id=5, status="ACTIVE")
    repository = MagicMock()
    repository.get_chunks_by_ids.side_effect = lambda ids: tuple(chunks[key] for key in ids)
    service = retrieval.RetrievalService(
        session, repository=repository, settings=SimpleNamespace(
            embedding_dimensions=3, rag_answer_prompt_version='rag-answer-v2',
        ),
        principal_loader=lambda _: actor,
    )
    monkeypatch.setattr(service, "_acl_for", lambda **_: retrieval.RetrievalAcl(
        tenant_id=7, property_ids=frozenset({9}),
        visibilities=frozenset({"RESIDENT"}), allow_all_properties=False,
    ))
    embedder, store, reranker, llm = (MagicMock() for _ in range(4))
    embedder.embed.return_value = ((0.1, 0.2, 0.3),)
    # Deliberately use non-ID order to detect accidental sorting during fallback.
    store.search.return_value = ({"chunk_id": 12, "score": 0.7}, {"chunk_id": 11, "score": 0.6})
    reranker.rerank.return_value = (0.9, 0.8)
    llm.structured_completion.return_value = {
        "status": "answered", "answer": "synthetic supported answer", "citation_chunk_ids": [12],
    }
    for name, provider in (
        ("build_embedding_provider", embedder), ("build_milvus_store", store),
        ("build_reranker_provider", reranker), ("build_rag_llm_provider", llm),
    ):
        monkeypatch.setattr(retrieval, name, lambda _settings, provider=provider: provider)
    return SimpleNamespace(
        query=lambda: service.query(
            actor=actor, question="synthetic question",
            request_id="unit-query", property_id=9,
        ),
        embedder=embedder, store=store, reranker=reranker, llm=llm, chunks=chunks,
    )


@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("stage", ("embedder", "store"))
def test_retrieval_provider_failure_does_not_generate(query_context, error, stage):
    ctx = query_context
    method = ctx.embedder.embed if stage == "embedder" else ctx.store.search
    method.side_effect = error("synthetic failure")
    answer = ctx.query()
    assert (answer.status, answer.answer, answer.citations) == (KNOWLEDGE_UNAVAILABLE, "知识助手暂时不可用", ())
    ctx.reranker.rerank.assert_not_called()
    ctx.llm.structured_completion.assert_not_called()
    if stage == "embedder":
        ctx.store.search.assert_not_called()


@pytest.mark.parametrize("error", ERRORS)
def test_reranker_failure_keeps_authorized_vector_order(query_context, error):
    ctx = query_context
    ctx.reranker.rerank.side_effect = error("synthetic failure")
    answer = ctx.query()
    assert answer.status == "answered"
    assert [item["document_id"] for item in answer.citations] == [32]
    prompt = ctx.llm.structured_completion.call_args.kwargs["prompt"]
    assert prompt.index("chunk-id:12") < prompt.index("chunk-id:11")
    assert 'tenant_id == 7' in ctx.store.search.call_args.kwargs["expr"]


@pytest.mark.parametrize("scores", [
    (0.9,), (float("nan"), 0.9), (0.9, float("inf")), (0.9, True),
    (None, 0.9), ("0.9", 0.8), (0.9, 10**400), None,
])
def test_custom_reranker_bad_scores_use_bounded_fallback(query_context, scores):
    ctx = query_context
    ctx.reranker.rerank.return_value = scores
    assert ctx.query().status == "answered"
    prompt = ctx.llm.structured_completion.call_args.kwargs["prompt"]
    assert prompt.index("chunk-id:12") < prompt.index("chunk-id:11")


def test_normal_rerank_threshold_and_order_are_preserved(query_context):
    ctx = query_context
    ctx.reranker.rerank.return_value = (0.01, 0.8)
    ctx.llm.structured_completion.return_value["citation_chunk_ids"] = [11]
    answer = ctx.query()
    assert answer.status == "answered"
    assert [item["document_id"] for item in answer.citations] == [31]
    assert "chunk-id:12" not in ctx.llm.structured_completion.call_args.kwargs["prompt"]


def test_no_relevant_candidates_does_not_generate(query_context):
    ctx = query_context
    ctx.reranker.rerank.return_value = (0.0, 0.0)
    assert ctx.query().status == INSUFFICIENT_KNOWLEDGE
    ctx.llm.structured_completion.assert_not_called()


def test_fallback_does_not_restore_acl_filtered_chunk(query_context):
    ctx = query_context
    ctx.chunks[11].tenant_id = 8
    ctx.reranker.rerank.side_effect = ProviderUnavailableError("synthetic")
    assert ctx.query().status == "answered"
    prompt = ctx.llm.structured_completion.call_args.kwargs["prompt"]
    assert "chunk-id:11" not in prompt
    assert "chunk-id:12" in prompt


@pytest.mark.parametrize("error", ERRORS)
def test_generation_provider_failure_is_unavailable(query_context, error):
    ctx = query_context
    ctx.llm.structured_completion.side_effect = error("synthetic failure")
    answer = ctx.query()
    assert answer.status == KNOWLEDGE_UNAVAILABLE
    assert answer.citations == ()


@pytest.mark.parametrize("citations", [[12, 999], [999], ["12"], [True], [0], [-1], [12.0]])
def test_invalid_citation_rejects_entire_answer(query_context, citations):
    ctx = query_context
    ctx.llm.structured_completion.return_value["citation_chunk_ids"] = citations
    answer = ctx.query()
    assert answer.status == KNOWLEDGE_UNAVAILABLE
    assert answer.answer != "synthetic supported answer"
    assert answer.citations == ()


@pytest.mark.parametrize("status", [KNOWLEDGE_UNAVAILABLE, INSUFFICIENT_KNOWLEDGE])
def test_explicit_nonanswer_status_is_not_reclassified(query_context, status):
    ctx = query_context
    ctx.llm.structured_completion.return_value = {"status": status, "answer": "", "citation_chunk_ids": []}
    answer = ctx.query()
    assert answer.status == status
    assert answer.citations == ()


@pytest.mark.parametrize("citations", [[999], [12, 999]])
def test_insufficient_clears_unused_answer_and_citations(query_context, citations):
    query_context.llm.structured_completion.return_value = {
        "status": INSUFFICIENT_KNOWLEDGE,
        "answer": "unused model text",
        "citation_chunk_ids": citations,
    }
    answer = query_context.query()
    assert (answer.status, answer.answer, answer.citations) == (INSUFFICIENT_KNOWLEDGE, "", ())


def test_answer_without_citation_is_insufficient(query_context):
    query_context.llm.structured_completion.return_value["citation_chunk_ids"] = []
    answer = query_context.query()
    assert (answer.status, answer.answer, answer.citations) == (INSUFFICIENT_KNOWLEDGE, "", ())


def test_valid_duplicate_citations_return_one_source(query_context):
    query_context.llm.structured_completion.return_value["citation_chunk_ids"] = [12, 12]
    answer = query_context.query()
    assert answer.status == "answered"
    assert len(answer.citations) == 1
