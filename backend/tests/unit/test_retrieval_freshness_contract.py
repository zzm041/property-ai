"""Logical SQLAlchemy transactions with synthetic data; NOT a MySQL integration test."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.core.database import TransactionContractError
from app.core.errors import AppError, IamErrorCode
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.knowledge import KNOWLEDGE_READ, KNOWLEDGE_UNAVAILABLE
from app.service import retrieval


@pytest.fixture
def context(monkeypatch):
    session = Session(expire_on_commit=False)
    scope = OrganizationScope(ScopeType.PROPERTY, 7, 9)
    actor = Principal(5, "ACTIVE", "synthetic", (RoleGrant("RESIDENT", scope, frozenset({KNOWLEDGE_READ})),))
    state = {"actor": actor, "loads": 0}
    docs = {key + 20: NS(id=key + 20, title=f"title {key}", tenant_id=7, property_id=9,
                        status="ACTIVE", visibility="RESIDENT", current_version=1) for key in (11, 12)}
    chunks = {key: NS(id=key, document_id=key + 20, tenant_id=7, property_id=9,
                      visibility="RESIDENT", version=1, excerpt=f"passage {key}", source_locator=f"para:{key}") for key in (11, 12)}

    def touch():
        # Simulate SQLAlchemy autobegin on a repository read without connecting.
        if not session.in_transaction():
            session.begin()

    def get_document(_model, key):
        touch()
        return docs.get(key)

    def get_chunks(ids):
        touch()
        return tuple(chunks[key] for key in ids if key in chunks)

    def load_principal(_user_id):
        touch()
        state["loads"] += 1
        if state["actor"].status != "ACTIVE":
            raise AppError(code=IamErrorCode.FORBIDDEN, message="synthetic revoked", status_code=403)
        return state["actor"]

    def acl_for(*, actor, **_):
        touch()
        if not actor.has_permission(KNOWLEDGE_READ, scope):
            raise AppError(code=IamErrorCode.FORBIDDEN, message="synthetic revoked", status_code=403)
        return retrieval.RetrievalAcl(7, frozenset({9}), frozenset({"RESIDENT"}), False)

    monkeypatch.setattr(session, "get", get_document)
    expire = MagicMock(wraps=session.expire_all)
    monkeypatch.setattr(session, "expire_all", expire)
    repo = MagicMock()
    repo.get_chunks_by_ids.side_effect = get_chunks
    service = retrieval.RetrievalService(session, repository=repo, settings=NS(embedding_dimensions=3, rag_answer_prompt_version='rag-answer-v2'))
    service._principal_loader = load_principal
    monkeypatch.setattr(service, "_acl_for", acl_for)
    embedder, store, reranker, llm = (MagicMock() for _ in range(4))
    embedder.embed.return_value = ((0.1, 0.2, 0.3),)
    store.search.return_value = ({"chunk_id": 12}, {"chunk_id": 11})
    reranker.rerank.return_value = (0.9, 0.8)
    answer = {"status": "answered", "answer": "synthetic supported answer", "citation_chunk_ids": [12]}
    llm.structured_completion.return_value = answer
    for name, provider in (("build_embedding_provider", embedder), ("build_milvus_store", store),
                           ("build_reranker_provider", reranker), ("build_rag_llm_provider", llm)):
        monkeypatch.setattr(retrieval, name, lambda _, provider=provider: provider)
    yield NS(session=session, state=state, actor=actor, docs=docs, chunks=chunks, service=service,
             embedder=embedder, store=store, reranker=reranker, llm=llm, expire=expire, answer=answer,
             query=lambda: service.query(actor=actor, question="synthetic", request_id="unit-fresh", property_id=9))
    session.close()


def test_no_provider_holds_the_query_session_transaction(context):
    c = context
    for method in (c.embedder.embed, c.store.search, c.reranker.rerank, c.llm.structured_completion):
        result = method.return_value
        def check(*args, result=result, **kwargs):
            assert not c.session.in_transaction(), "Provider holds an open DB transaction"
            return result
        method.side_effect = check
    result = c.query()
    assert result.status == "answered"
    assert result.citations[0]["title"] == "title 12"
    assert not c.session.in_transaction()
    assert c.state["loads"] >= 4
    assert c.expire.call_count >= 4


@pytest.mark.parametrize("stage", ["reranker", "llm"])
@pytest.mark.parametrize("change", ["archive", "version", "title", "visibility", "property", "text", "locator", "delete_chunk", "delete_document"])
def test_changed_selected_evidence_never_returns_stale_answer(context, stage, change):
    c = context
    method = c.reranker.rerank if stage == "reranker" else c.llm.structured_completion
    result = method.return_value
    def mutate(*args, **kwargs):
        # Chunk 11 is selected but not cited: its contents still informed the LLM.
        if change == "archive": c.docs[31].status = "ARCHIVED"
        elif change == "version": c.docs[31].current_version = 2
        elif change == "title": c.docs[31].title = "replacement title"
        elif change == "visibility": c.docs[31].visibility = "STAFF_ONLY"
        elif change == "property": c.docs[31].property_id = 10
        elif change == "text": c.chunks[11].excerpt = "changed passage"
        elif change == "locator": c.chunks[11].source_locator = "changed location"
        elif change == "delete_chunk": del c.chunks[11]
        elif change == "delete_document": del c.docs[31]
        return result
    method.side_effect = mutate
    response = c.query()
    assert response.status == KNOWLEDGE_UNAVAILABLE
    assert response.answer != "synthetic supported answer" and response.citations == ()
    if stage == "reranker":
        c.llm.structured_completion.assert_not_called()
    assert not c.session.in_transaction()


@pytest.mark.parametrize("stage", ["store", "reranker", "llm"])
@pytest.mark.parametrize("disabled", [False, True])
def test_current_identity_is_reloaded_between_provider_stages(context, stage, disabled):
    c = context
    method = {"store": c.store.search, "reranker": c.reranker.rerank, "llm": c.llm.structured_completion}[stage]
    result = method.return_value
    def revoke(*args, **kwargs):
        c.state["actor"] = Principal(5, "DISABLED" if disabled else "ACTIVE", "synthetic", ())
        return result
    method.side_effect = revoke
    with pytest.raises(AppError) as error:
        c.query()
    assert error.value.status_code == 403
    if stage != "llm": c.llm.structured_completion.assert_not_called()
    assert not c.session.in_transaction()


def test_query_does_not_commit_or_rollback_callers_transaction(context):
    c = context
    transaction = c.session.begin()
    with pytest.raises(TransactionContractError):
        c.query()
    assert c.session.get_transaction() is transaction and transaction.is_active
    c.embedder.embed.assert_not_called()


def test_document_scope_mismatch_is_filtered_before_reranking(context):
    c = context
    for doc in c.docs.values(): doc.property_id = 10
    assert c.query().status == "insufficient_knowledge"
    c.reranker.rerank.assert_not_called()
    c.llm.structured_completion.assert_not_called()
    assert not c.session.in_transaction()


def test_no_evidence_closes_its_read_transaction(context):
    context.store.search.return_value = ()
    assert context.query().status == "insufficient_knowledge"
    assert not context.session.in_transaction()
