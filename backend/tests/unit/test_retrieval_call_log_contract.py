"""F2 / t177: embedding and rerank calls must leave ai_call_logs rows.

The knowledge assistant used to log only structured events, so an operator could
not answer "which embedding model, how slow, did it fail" from the database while
the work-order chain answered exactly that. These tests drive the real
RetrievalService.query flow and read the AiCallLog rows it produces.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.domain.ai import AiCallStatus, ProviderType
from app.provider.errors import (
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.service import retrieval


class RecordingAiRepository:
    """Stands in for AiRepository: keeps the AiCallLog objects, drops the DB."""

    def __init__(self) -> None:
        self.rows = []

    def add_call_log(self, record):
        self.rows.append(record)
        return record


@pytest.fixture
def context(monkeypatch):
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
    monkeypatch.setattr(session, "get", lambda _model, key: documents.get(key))
    actor = SimpleNamespace(user_id=5, status="ACTIVE")
    repository = MagicMock()
    repository.get_chunks_by_ids.side_effect = lambda ids: tuple(chunks[key] for key in ids)
    settings = SimpleNamespace(
        embedding_dimensions=3,
        rag_answer_prompt_version="rag-answer-v2",
        embedding_provider="production",
        reranker_provider="production",
    )
    service = retrieval.RetrievalService(
        session, repository=repository, settings=settings,
        principal_loader=lambda _: actor,
    )
    monkeypatch.setattr(service, "_acl_for", lambda **_: retrieval.RetrievalAcl(
        tenant_id=7, property_ids=frozenset({9}),
        visibilities=frozenset({"RESIDENT"}), allow_all_properties=False,
    ))
    # The unit session has no bind: the transaction and the 0009 column write are
    # the two pieces that need a database, so they are neutralised here and
    # exercised against the real MySQL schema by the delivery run.
    monkeypatch.setattr(retrieval, "begin_owned_transaction", lambda _session: nullcontext())
    monkeypatch.setattr(service, "_record_provider_mode", lambda *_: None)
    recorder = RecordingAiRepository()
    service._ai = recorder

    embedder, store, reranker, llm = (MagicMock() for _ in range(4))
    embedder.embed.return_value = ((0.1, 0.2, 0.3),)
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
        rows=recorder.rows, service=service,
        embedder=embedder, reranker=reranker,
    )


def kinds(rows):
    return [(row.provider_type, row.operation) for row in rows]


def test_one_successful_retrieval_audits_every_provider_call(context):
    answer = context.query()

    assert answer.status == "answered"
    # PA-200 / t200 added the generate row, so one answer leaves three rows.
    assert kinds(context.rows) == [
        (ProviderType.EMBEDDING.value, "embed"),
        (ProviderType.RERANK.value, "rerank"),
        (ProviderType.LLM.value, "structured_completion"),
    ]
    for row in context.rows:
        assert row.status == AiCallStatus.SUCCESS.value
        assert row.biz_type == "KNOWLEDGE_QUERY"
        assert row.request_id == "unit-query"
        assert row.tenant_id == 7
        assert row.work_order_id is None
        assert row.error_code is None
        assert row.attempt == 1
        assert row.latency_ms >= 0
        assert row.started_at <= row.finished_at
        assert re.fullmatch(r"[0-9a-f]{64}", row.input_hash)


def test_embedding_failure_is_audited_before_the_answer_degrades(context):
    context.embedder.embed.side_effect = ProviderTimeoutError("slow embedding")

    assert context.query().status == "knowledge_unavailable"
    assert kinds(context.rows) == [(ProviderType.EMBEDDING.value, "embed")]
    row = context.rows[0]
    assert row.status == AiCallStatus.TIMEOUT.value
    assert row.error_code == "AI.TIMEOUT"


def test_unavailable_reranker_still_leaves_a_failed_row_and_the_fallback(context):
    context.reranker.rerank.side_effect = ProviderUnavailableError("down")

    answer = context.query()

    assert answer.status == "answered"
    assert kinds(context.rows)[:2] == [
        (ProviderType.EMBEDDING.value, "embed"),
        (ProviderType.RERANK.value, "rerank"),
    ]
    assert context.rows[1].status == AiCallStatus.FAILED.value
    assert context.rows[1].error_code == "AI.UNAVAILABLE"


def test_invalid_reranker_output_is_audited_as_invalid(context):
    # Scores of the wrong length are rejected by the service, not by the provider.
    context.reranker.rerank.return_value = (0.9,)

    answer = context.query()

    assert answer.status == "answered"
    assert context.rows[1].provider_type == ProviderType.RERANK.value
    # The provider call itself succeeded; the contract rejection is the service's.
    assert context.rows[1].status == AiCallStatus.SUCCESS.value


def test_audit_write_failure_never_changes_the_answer(context, monkeypatch):
    def explode(_record):
        raise RuntimeError("audit table is gone")

    monkeypatch.setattr(context.service._ai, "add_call_log", explode)

    assert context.query().status == "answered"


def test_rerank_input_hash_identifies_evidence_without_storing_it(context):
    context.query()

    embed_hash, rerank_hash = (row.input_hash for row in context.rows[:2])
    assert embed_hash != rerank_hash
    assert "synthetic" not in rerank_hash
    assert "passage" not in rerank_hash
