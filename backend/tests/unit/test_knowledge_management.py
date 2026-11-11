from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError, IamErrorCode, KnowledgeErrorCode
from app.domain.governance import AUDIT_ACTION_POLICIES, AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.knowledge import (
    KNOWLEDGE_MANAGE,
    KnowledgeDocumentStatus,
    KnowledgeIngestionStatus,
)
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
        settings=MagicMock(durable_tasks_enabled=False),
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


def test_management_list_batches_versions_and_jobs_instead_of_querying_per_document() -> None:
    actor = _principal(
        OrganizationScope(ScopeType.PROPERTY, tenant_id=10, property_id=20)
    )
    service, repository, properties, _audit = _service(actor)
    properties.get_property_by_id.return_value = SimpleNamespace(id=20, tenant_id=10)
    documents = (
        SimpleNamespace(id=71, tenant_id=10, property_id=20),
        SimpleNamespace(id=72, tenant_id=10, property_id=20),
    )
    version_71 = SimpleNamespace(document_id=71, version=2)
    version_72 = SimpleNamespace(document_id=72, version=1)
    job_71 = SimpleNamespace(document_id=71, version=2)
    repository.list_documents_for_management.return_value = documents
    repository.list_versions_for_documents.return_value = (version_71, version_72)
    repository.list_ingestion_jobs_for_documents.return_value = (job_71,)

    details = service.list_managed(actor=actor, property_id=20)

    assert details == (
        (documents[0], (version_71,), (job_71,)),
        (documents[1], (version_72,), ()),
    )
    repository.list_versions_for_documents.assert_called_once_with((71, 72))
    repository.list_ingestion_jobs_for_documents.assert_called_once_with((71, 72))
    repository.list_versions.assert_not_called()
    repository.list_ingestion_jobs.assert_not_called()


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
    repository.next_version_for_update.return_value = 2

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


def test_archived_status_is_checked_only_after_authorization() -> None:
    actor = _principal(
        OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=10,
            property_id=99,
        )
    )
    service, repository, _properties, _audit = _service(actor)
    repository.get_document_for_update.return_value = KnowledgeDocument(
        id=71,
        tenant_id=10,
        property_id=20,
        title="archived",
        document_type="GUIDE",
        visibility="RESIDENT",
        status="ARCHIVED",
        current_version=1,
        created_by_user_id=55,
    )

    with pytest.raises(AppError) as exc_info:
        service._create_version(
            actor=actor,
            document_id=71,
            source_text="Unauthorized content.",
            request_id="knowledge-version-idor",
            claimed_tenant_id=None,
            claimed_property_id=None,
        )

    assert exc_info.value.code == IamErrorCode.FORBIDDEN


def test_archived_document_new_version_enters_reactivation_after_authorization() -> None:
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

    repository.next_version_for_update.return_value = 2
    repository.add_version.side_effect = lambda item: item
    repository.add_job.side_effect = lambda item: item

    version, job = service._create_version(
        actor=actor,
        document_id=71,
        source_text="A reviewed replacement version.",
        request_id="knowledge-version-archived",
        claimed_tenant_id=10,
        claimed_property_id=None,
    )

    document = repository.get_document_for_update.return_value
    assert document.status == KnowledgeDocumentStatus.DRAFT.value
    assert document.current_version == 1
    assert version.version == 2
    assert job.status == KnowledgeIngestionStatus.PENDING.value
    audit_details = _audit.append.call_args.kwargs["details"]
    assert audit_details["reactivating_archived"] is True


def test_older_inflight_version_cannot_activate_after_new_reactivation_target_exists() -> None:
    actor = _principal(OrganizationScope(ScopeType.TENANT, tenant_id=10))
    service, repository, _properties, _audit = _service(actor)
    document = KnowledgeDocument(
        id=71,
        tenant_id=10,
        property_id=None,
        title="reactivating",
        document_type="GUIDE",
        visibility="RESIDENT",
        status=KnowledgeDocumentStatus.DRAFT.value,
        current_version=1,
        created_by_user_id=actor.user_id,
    )
    version = SimpleNamespace(version=2, status="PENDING")
    job = SimpleNamespace(status="PROCESSING", error_code=None, finished_at=None)
    repository.latest_version_for_update.return_value = 3

    service._activate_version(document, version, job)

    assert document.status == KnowledgeDocumentStatus.DRAFT.value
    assert document.current_version == 1
    assert version.status == "PENDING"
    assert job.status == KnowledgeIngestionStatus.FAILED.value
    assert job.error_code == KnowledgeErrorCode.CONFLICT


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


def test_next_version_uses_a_current_locking_read() -> None:
    session = MagicMock()
    session.scalar.return_value = 4
    repository = KnowledgeRepository(session)

    assert repository.next_version_for_update(71) == 5

    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_document_versions.document_id = 71" in sql
    assert "ORDER BY knowledge_document_versions.version DESC" in sql
    assert "LIMIT 1" in sql
    assert "FOR UPDATE" in sql
