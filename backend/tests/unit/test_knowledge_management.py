from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, IamErrorCode, KnowledgeErrorCode
from app.domain.audit import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.knowledge import KNOWLEDGE_MANAGE
from app.model.knowledge import KnowledgeDocument
from app.repository.knowledge import KnowledgeRepository
from app.service.knowledge import KnowledgeService


def _principal(scope: OrganizationScope) -> Principal:
    return Principal(
        user_id=41,
        status="ACTIVE",
        display_name="knowledge manager",
        grants=(
            RoleGrant(
                role_code="PROPERTY_ADMIN",
                scope=scope,
                permissions=frozenset({KNOWLEDGE_MANAGE}),
            ),
        ),
    )


def _service(actor: Principal) -> tuple[KnowledgeService, MagicMock, MagicMock, MagicMock]:
    repository = MagicMock()
    properties = MagicMock()
    audit = MagicMock()

    def add_document(record):
        record.id = 71
        return record

    def add_version(record):
        record.id = 81
        return record

    def add_job(record):
        record.id = 91
        return record

    repository.add_document.side_effect = add_document
    repository.add_version.side_effect = add_version
    repository.add_job.side_effect = add_job
    service = KnowledgeService(
        MagicMock(),
        repository=repository,
        property_repository=properties,
        audit_service=audit,
        principal_loader=lambda _user_id: actor,
        settings=MagicMock(),
    )
    return service, repository, properties, audit


def test_knowledge_audit_policies_cover_version_and_activation() -> None:
    for action in (
        AuditAction.KNOWLEDGE_DOCUMENT_VERSION_CREATED,
        AuditAction.KNOWLEDGE_DOCUMENT_ACTIVATED,
    ):
        assert (
            AUDIT_ACTION_POLICIES[action].resource_type
            is AuditResourceType.KNOWLEDGE_DOCUMENT
        )


def test_tenant_manager_can_create_tenant_document() -> None:
    actor = _principal(OrganizationScope(ScopeType.TENANT, tenant_id=10))
    service, repository, properties, audit = _service(actor)
    properties.get_tenant.return_value = SimpleNamespace(id=10, status="ACTIVE")

    document, job = service._create_document(
        actor=actor,
        property_id=None,
        title="  enterprise guide  ",
        document_type="GUIDE",
        visibility="RESIDENT",
        source_text="The service desk is open every day.",
        request_id="knowledge-create",
        claimed_tenant_id=10,
    )

    assert document.tenant_id == 10
    assert document.property_id is None
    assert document.title == "enterprise guide"
    assert job.property_id is None
    assert repository.add_version.call_args.args[0].version == 1
    audit_call = audit.append.call_args.kwargs
    assert audit_call["action"] is AuditAction.KNOWLEDGE_DOCUMENT_CREATED
    assert audit_call["scope_type"] is ScopeType.TENANT


def test_property_manager_cannot_create_tenant_document() -> None:
    actor = _principal(
        OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=10,
            property_id=20,
        )
    )
    service, _repository, properties, _audit = _service(actor)
    properties.get_tenant.return_value = SimpleNamespace(id=10, status="ACTIVE")

    with pytest.raises(AppError) as exc_info:
        service._create_document(
            actor=actor,
            property_id=None,
            title="enterprise guide",
            document_type="GUIDE",
            visibility="RESIDENT",
            source_text="Tenant-wide content.",
            request_id="knowledge-create-denied",
            claimed_tenant_id=10,
        )

    assert exc_info.value.code == IamErrorCode.FORBIDDEN


def test_management_list_passes_exact_target_permissions_to_repository() -> None:
    actor = _principal(
        OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=10,
            property_id=20,
        )
    )
    service, repository, properties, _audit = _service(actor)
    properties.get_property_by_id.return_value = SimpleNamespace(id=20, tenant_id=10)
    repository.list_documents_for_management.return_value = ()

    assert service.list_managed(actor=actor, property_id=20) == ()

    call = repository.list_documents_for_management.call_args.kwargs
    assert call["include_property_documents"] is True
    assert call["include_tenant_documents"] is False
    assert call["tenant_id"] == 10
    assert call["property_id"] == 20


def test_create_version_locks_document_and_records_pending_job() -> None:
    actor = _principal(
        OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=10,
            property_id=20,
        )
    )
    service, repository, _properties, audit = _service(actor)
    document = KnowledgeDocument(
        id=71,
        tenant_id=10,
        property_id=20,
        title="property guide",
        document_type="GUIDE",
        visibility="RESIDENT",
        status="ACTIVE",
        current_version=1,
        created_by_user_id=actor.user_id,
    )
    repository.get_document_for_update.return_value = document
    repository.next_version.return_value = 2

    version, job = service._create_version(
        actor=actor,
        document_id=document.id,
        source_text="Second version content.",
        request_id="knowledge-version",
        claimed_tenant_id=10,
        claimed_property_id=20,
    )

    assert version.version == 2
    assert version.status == "PENDING"
    assert job.version == 2
    repository.get_document_for_update.assert_called_once_with(document.id)
    audit_call = audit.append.call_args.kwargs
    assert audit_call["action"] is AuditAction.KNOWLEDGE_DOCUMENT_VERSION_CREATED
    assert audit_call["details"]["version"] == 2


def test_archived_document_rejects_new_version() -> None:
    actor = _principal(OrganizationScope(ScopeType.TENANT, tenant_id=10))
    service, repository, _properties, _audit = _service(actor)
    repository.get_document_for_update.return_value = KnowledgeDocument(
        id=71,
        tenant_id=10,
        property_id=None,
        title="archived",
        document_type="GUIDE",
        visibility="RESIDENT",
        status="ARCHIVED",
        current_version=1,
        created_by_user_id=actor.user_id,
    )

    with pytest.raises(AppError) as exc_info:
        service._create_version(
            actor=actor,
            document_id=71,
            source_text="Should not be accepted.",
            request_id="knowledge-version-archived",
            claimed_tenant_id=10,
            claimed_property_id=None,
        )

    assert exc_info.value.code == KnowledgeErrorCode.ARCHIVED


def test_repository_filters_authorized_targets_before_limit() -> None:
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    repository = KnowledgeRepository(session)

    repository.list_documents_for_management(
        tenant_id=10,
        property_id=20,
        include_tenant_documents=False,
        include_property_documents=True,
        after_id=100,
        limit=2,
    )

    statement = session.scalars.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_documents.tenant_id = 10" in sql
    assert "knowledge_documents.property_id = 20" in sql
    assert "knowledge_documents.id > 100" in sql
    assert "knowledge_documents.status != 'ARCHIVED'" in sql
    assert "LIMIT 2" in sql
