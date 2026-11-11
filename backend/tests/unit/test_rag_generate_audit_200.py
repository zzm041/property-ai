"""PA-200 / t200: the RAG answer call must leave its own ai_call_logs row.

t177 audited embed and rerank, so an operator could see two of the three calls a
knowledge answer makes. The generate call — the one that costs the most tokens
and carries the prompt version — was only in structured logs.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.domain.ai import AiCallStatus, ProviderType
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError
from app.service import retrieval


class RecordingAiRepository:
    def __init__(self) -> None:
        self.rows = []
        self.sessions = []

    def add_call_log(self, record):
        self.rows.append(record)
        return record


def _build(monkeypatch, *, llm_result=None, llm_error=None):
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
        rag_llm_provider="inherit",
        llm_provider="production",
    )
    service = retrieval.RetrievalService(
        session, repository=repository, settings=settings,
        principal_loader=lambda _: actor,
    )
    monkeypatch.setattr(service, "_acl_for", lambda **_: retrieval.RetrievalAcl(
        tenant_id=7, property_ids=frozenset({9}),
        visibilities=frozenset({"RESIDENT"}), allow_all_properties=False,
    ))
    monkeypatch.setattr(retrieval, "begin_owned_transaction", lambda _session: nullcontext())
    monkeypatch.setattr(service, "_record_provider_mode", lambda *_: None)
    recorder = RecordingAiRepository()
    service._ai = recorder

    embedder, store, reranker, llm = (MagicMock() for _ in range(4))
    embedder.embed.return_value = ((0.1, 0.2, 0.3),)
    store.search.return_value = ({"chunk_id": 12, "score": 0.7}, {"chunk_id": 11, "score": 0.6})
    reranker.rerank.return_value = (0.9, 0.8)
    if llm_error is not None:
        llm.structured_completion.side_effect = llm_error
    else:
        llm.structured_completion.return_value = llm_result or {
            "status": "answered", "answer": "synthetic supported answer",
            "citation_chunk_ids": [12],
        }
    for name, provider in (
        ("build_embedding_provider", embedder), ("build_milvus_store", store),
        ("build_reranker_provider", reranker), ("build_rag_llm_provider", llm),
    ):
        monkeypatch.setattr(retrieval, name, lambda _settings, provider=provider: provider)

    query = lambda: service.query(  # noqa: E731
        actor=actor, question="synthetic question", request_id="unit-query-200", property_id=9,
    )
    return SimpleNamespace(query=query, rows=recorder.rows, service=service, llm=llm,
                           session=session, recorder=recorder)


def _kinds(rows):
    return [(row.provider_type, row.operation) for row in rows]


def test_one_answer_audits_embed_rerank_and_generate(monkeypatch):
    context = _build(monkeypatch)

    answer = context.query()

    assert answer.status == "answered"
    assert _kinds(context.rows) == [
        (ProviderType.EMBEDDING.value, "embed"),
        (ProviderType.RERANK.value, "rerank"),
        (ProviderType.LLM.value, "structured_completion"),
    ]
    generate = context.rows[2]
    assert generate.status == AiCallStatus.SUCCESS.value
    assert generate.error_code is None
    assert generate.prompt_version == "rag-answer-v2"
    assert generate.request_id == "unit-query-200"
    assert generate.tenant_id == 7
    assert generate.biz_type == "KNOWLEDGE_QUERY"
    assert generate.work_order_id is None
    assert generate.latency_ms >= 0
    assert generate.started_at <= generate.finished_at
    assert re.fullmatch(r"[0-9a-f]{64}", generate.input_hash)


def test_generate_failure_is_audited_then_degrades(monkeypatch):
    context = _build(monkeypatch, llm_error=ProviderUnavailableError("adapter down"))

    answer = context.query()

    assert answer.status == "knowledge_unavailable"
    assert _kinds(context.rows) == [
        (ProviderType.EMBEDDING.value, "embed"),
        (ProviderType.RERANK.value, "rerank"),
        (ProviderType.LLM.value, "structured_completion"),
    ]
    generate = context.rows[2]
    assert generate.status == AiCallStatus.FAILED.value
    assert generate.error_code == "AI.UNAVAILABLE"
    assert generate.prompt_version == "rag-answer-v2"


def test_generate_timeout_is_audited_as_timeout(monkeypatch):
    context = _build(monkeypatch, llm_error=ProviderTimeoutError("slow answer"))

    assert context.query().status == "knowledge_unavailable"
    assert context.rows[-1].status == AiCallStatus.TIMEOUT.value
    assert context.rows[-1].error_code == "AI.TIMEOUT"


def test_answer_that_breaks_the_contract_adds_a_validation_row(monkeypatch):
    # Syntactically valid JSON, but `citations` is missing the required field.
    context = _build(monkeypatch, llm_result={"status": "answered"})

    answer = context.query()

    assert answer.status == "knowledge_unavailable"
    assert _kinds(context.rows) == [
        (ProviderType.EMBEDDING.value, "embed"),
        (ProviderType.RERANK.value, "rerank"),
        (ProviderType.LLM.value, "structured_completion"),
        (ProviderType.LLM.value, "payload_validation"),
    ]
    assert context.rows[2].status == AiCallStatus.SUCCESS.value
    validation = context.rows[3]
    assert validation.status == AiCallStatus.INVALID.value
    assert validation.error_code == "AI.INVALID_OUTPUT"
    assert validation.prompt_version == "rag-answer-v2"


def test_empty_answer_with_the_answered_status_is_a_validation_row(monkeypatch):
    context = _build(monkeypatch, llm_result={
        "status": "answered", "answer": "   ", "citation_chunk_ids": [12],
    })

    assert context.query().status == "knowledge_unavailable"
    assert context.rows[-1].operation == "payload_validation"
    assert context.rows[-1].status == AiCallStatus.INVALID.value


def test_audit_write_runs_in_its_own_session_when_the_request_transaction_is_open(monkeypatch):
    """The t186 trap: an open request transaction must not swallow the row."""

    context = _build(monkeypatch)
    closed: list[bool] = []

    class FakeOwnedSession:
        def in_transaction(self) -> bool:
            return False

        def get_bind(self):
            return None

        def close(self) -> None:
            closed.append(True)

    owned = FakeOwnedSession()
    seen: list[object] = []

    monkeypatch.setattr(context.session, "in_transaction", lambda: True)
    # A real request session is bound; `Session.get_bind()` raises when unbound.
    monkeypatch.setattr(context.session, "get_bind", lambda: object())
    monkeypatch.setattr(retrieval, "Session", lambda **_kwargs: owned)
    monkeypatch.setattr(
        retrieval, "AiRepository", lambda session: (seen.append(session), context.recorder)[1]
    )

    assert context.query().status == "answered"

    generate = context.rows[2]
    assert generate.operation == "structured_completion"
    assert generate.status == AiCallStatus.SUCCESS.value
    # Every audit row went through the sibling session, not the request one.
    assert seen == [owned, owned, owned]
    assert closed == [True, True, True]
