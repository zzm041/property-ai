"""Synthetic ACL/query/API tests, not real MySQL or model acceptance."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app.api.v1.assistant import get_current_principal, get_retrieval_service, router
from app.core.errors import AppError
from app.core.time import utc_now
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.knowledge import KNOWLEDGE_READ
from app.repository.resident import ResidentBindingRepository
from app.service import retrieval


def grant(role, *, tenant=7, property_id=9, kind=ScopeType.PROPERTY):
    scope = OrganizationScope(kind, None if kind is ScopeType.SYSTEM else tenant,
                              property_id if kind is ScopeType.PROPERTY else None)
    return RoleGrant(role, scope, frozenset({KNOWLEDGE_READ}))


@pytest.fixture
def case(monkeypatch):
    sessions = []
    def build(*grants, resident=False, employee_status=None, employee_tenant=7, service_properties=(9,)):
        session = Session(expire_on_commit=False)
        sessions.append(session)
        actor = Principal(5, "ACTIVE", "synthetic", grants)
        state = NS(resident=resident, service_properties=service_properties)
        residents, employees, properties, repository = (MagicMock() for _ in range(4))
        residents.has_active_property_relation.side_effect = lambda **_: state.resident
        residents.list_active_relations_for_user.return_value = ()
        employee = None if employee_status is None else NS(id=8, tenant_id=employee_tenant, status=employee_status)
        employees.get_employee_for_user.return_value = employee
        employees.list_active_property_ids_for_employee.side_effect = lambda **_: state.service_properties
        properties.get_property_by_id.side_effect = lambda pid: NS(id=pid, tenant_id=7 if pid != 90 else 70)
        document = NS(id=31, tenant_id=7, property_id=9, visibility="RESIDENT", current_version=1, status="ACTIVE", title="synthetic")
        chunk = NS(id=11, document_id=31, tenant_id=7, property_id=9, visibility="RESIDENT", version=1, excerpt="safe", source_locator="p1")
        repository.get_chunks_by_ids.side_effect = lambda ids: (chunk,) if 11 in ids else ()
        monkeypatch.setattr(session, "get", lambda *args: document)
        service = retrieval.RetrievalService(session, repository=repository, resident_repository=residents,
                    employee_repository=employees, property_repository=properties,
                    settings=NS(embedding_dimensions=3), principal_loader=lambda _: actor)
        providers = [MagicMock() for _ in range(4)]
        embedder, store, reranker, llm = providers
        embedder.embed.return_value = ((0.1, 0.2, 0.3),)
        store.search.return_value = ({"chunk_id": 11},)
        reranker.rerank.return_value = (0.9,)
        llm.structured_completion.return_value = dict(status="answered", answer="safe", citation_chunk_ids=[11])
        for name, provider in zip(("embedding_provider", "milvus_store", "reranker_provider", "rag_llm_provider"), providers):
            monkeypatch.setattr(retrieval, "build_" + name, lambda _, provider=provider: provider)
        return NS(actor=actor, service=service, session=session, state=state, employee=employee,
                  residents=residents, employees=employees, chunk=chunk, document=document,
                  embedder=embedder, store=store, reranker=reranker, llm=llm,
                  acl=lambda **kw: service._acl_for(actor=actor, property_id=kw.pop("property_id", 9), claimed_tenant_id=kw.pop("tenant_id", None), **kw),
                  query=lambda **kw: service.query(actor=actor, question="synthetic", request_id="access-unit", property_id=9, **kw))
    yield build
    for session in sessions: session.close()


def test_stale_resident_grant_without_current_house_is_denied(case):
    c = case(grant("RESIDENT"))
    with pytest.raises(AppError) as error: c.query()
    assert error.value.status_code == 403
    c.embedder.embed.assert_not_called()


@pytest.mark.parametrize("employee_status,tenant,properties", [(None, 7, (9,)), ("INACTIVE", 7, (9,)), ("ACTIVE", 70, (9,)), ("ACTIVE", 7, ())])
def test_stale_staff_grant_requires_current_same_tenant_employee(case, employee_status, tenant, properties):
    c = case(grant("STAFF"), employee_status=employee_status, employee_tenant=tenant, service_properties=properties)
    with pytest.raises(AppError): c.query()
    c.embedder.embed.assert_not_called()


def test_resident_relationship_cannot_activate_stale_staff_role(case):
    c = case(grant("STAFF"), resident=True)
    with pytest.raises(AppError): c.query()


def test_employee_relationship_cannot_activate_stale_resident_role(case):
    c = case(grant("RESIDENT"), employee_status="ACTIVE")
    with pytest.raises(AppError): c.query()


def test_current_resident_uses_targeted_relation_and_public_visibility(case):
    c = case(grant("RESIDENT"), resident=True)
    acl = c.acl()
    assert acl.visibilities == frozenset({"RESIDENT"})
    assert acl.property_ids == frozenset({0, 9})
    assert c.residents.has_active_property_relation.call_args.kwargs["tenant_id"] == 7
    assert c.residents.has_active_property_relation.call_args.kwargs["property_id"] == 9
    c.residents.list_active_relations_for_user.assert_not_called()


@pytest.mark.parametrize("role", ["STAFF", "SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN", "SYSTEM_ADMIN"])
def test_default_public_and_explicit_property_visibilities(case, role):
    kind = ScopeType.SYSTEM if role == "SYSTEM_ADMIN" else ScopeType.TENANT if role == "TENANT_ADMIN" else ScopeType.PROPERTY
    c = case(grant(role, kind=kind), employee_status="ACTIVE" if role == "STAFF" else None)
    assert c.acl().visibilities == frozenset({"RESIDENT"})
    assert "STAFF_ONLY" in c.acl(audience="property").visibilities
    if role == "STAFF": assert "PROPERTY" not in c.acl(audience="property").visibilities


def test_dual_role_does_not_leak_internal_context_by_default(case):
    c = case(grant("RESIDENT"), grant("STAFF"), resident=True, employee_status="ACTIVE")
    c.chunk.visibility = c.document.visibility = "STAFF_ONLY"
    assert c.query().status == "insufficient_knowledge"
    c.llm.structured_completion.assert_not_called()
    assert '"STAFF_ONLY"' not in c.store.search.call_args.kwargs["expr"]
    assert c.query(audience="property").status == "answered"


def test_public_resident_cannot_request_property_evidence(case):
    c = case(grant("RESIDENT"), resident=True)
    with pytest.raises(AppError): c.query(audience="property")
    c.embedder.embed.assert_not_called()


@pytest.mark.parametrize("role", ["RESIDENT", "STAFF", "PROPERTY_ADMIN", "SUPERVISOR"])
def test_broad_wrong_role_does_not_allow_tenant_wide_query(case, role):
    c = case(grant(role, kind=ScopeType.TENANT), resident=True, employee_status="ACTIVE")
    with pytest.raises(AppError): c.acl(property_id=None, tenant_id=7)


def test_valid_tenant_admin_can_query_its_tenant_but_not_another(case):
    c = case(grant("TENANT_ADMIN", kind=ScopeType.TENANT))
    assert c.acl(property_id=None, tenant_id=7, audience="property").allow_all_properties
    with pytest.raises(AppError): c.acl(property_id=None, tenant_id=70, audience="property")
    with pytest.raises(AppError): c.acl(property_id=90, audience="property")


def test_staff_role_from_another_property_does_not_enlarge_resident_visibility(case):
    c = case(grant("RESIDENT"), grant("STAFF", property_id=10), resident=True, employee_status="ACTIVE")
    assert c.acl().visibilities == frozenset({"RESIDENT"})
    with pytest.raises(AppError): c.acl(audience="property")


@pytest.mark.parametrize("stage", ["reranker", "llm"])
@pytest.mark.parametrize("role", ["RESIDENT", "STAFF"])
def test_relationship_revocation_during_query_prevents_answer(case, stage, role):
    c = case(grant(role), resident=role == "RESIDENT", employee_status="ACTIVE" if role == "STAFF" else None)
    method = c.reranker.rerank if stage == "reranker" else c.llm.structured_completion
    result = method.return_value
    def revoke(**kwargs):
        c.state.resident = False
        c.state.service_properties = ()
        return result
    method.side_effect = revoke
    with pytest.raises(AppError): c.query()
    if stage == "reranker": c.llm.structured_completion.assert_not_called()
    assert not c.session.in_transaction()


@pytest.mark.parametrize("audience", [None, "resident", "property", "invalid"])
def test_http_audience_default_and_validation(audience):
    app = FastAPI()
    app.include_router(router)
    service = MagicMock()
    service.query.return_value = retrieval.AssistantAnswer("insufficient_knowledge", "", ())
    app.dependency_overrides[get_retrieval_service] = lambda: service
    app.dependency_overrides[get_current_principal] = lambda: Principal(5, "ACTIVE", "unit", ())
    body = dict(question="synthetic", property_id=9)
    if audience is not None: body["audience"] = audience
    with TestClient(app) as client: response = client.post("/assistant/query", json=body)
    if audience == "invalid":
        assert response.status_code == 422
        service.query.assert_not_called()
    else:
        assert response.status_code == 200
        assert service.query.call_args.kwargs["audience"] == (audience or "resident")


def test_resident_exists_query_has_scope_lifetime_and_house_join():
    session = MagicMock()
    session.scalar.return_value = 1
    repo = ResidentBindingRepository(session)
    now = utc_now()
    assert repo.has_active_property_relation(user_id=5, tenant_id=7, property_id=9, now=now)
    stmt = session.scalar.call_args.args[0].compile(dialect=mysql.dialect())
    sql, params = str(stmt), stmt.params
    for term in ("valid_from <=", "valid_to IS NULL", "valid_to >", ".tenant_id =", ".property_id =", "JOIN houses", "LIMIT"):
        assert term in sql
    assert {5, 7, 9, "ACTIVE", now} <= set(params.values())
    session.scalar.return_value = None
    assert not repo.has_active_property_relation(user_id=5, tenant_id=7, property_id=9, now=now)
