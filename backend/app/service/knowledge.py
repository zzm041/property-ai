"""Knowledge document lifecycle and asynchronous ingestion."""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import transaction_context
from app.core.errors import (
    AppError,
    AuthErrorCode,
    ErrorCode,
    IamErrorCode,
    KnowledgeErrorCode,
)
from app.core.logging import get_logger
from app.core.time import utc_now
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.domain.knowledge import (
    KNOWLEDGE_MANAGE,
    KnowledgeDocumentStatus,
    KnowledgeDocumentType,
    KnowledgeIngestionStatus,
    KnowledgeVersionStatus,
    KnowledgeVisibility,
    chunk_text,
    hash_content,
    milvus_property_id,
)
from app.model.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
)
from app.provider.errors import ProviderTimeoutError, ProviderUnavailableError
from app.provider.factory import build_embedding_provider, build_milvus_store
from app.provider.knowledge_parser import KnowledgeParseError, TextKnowledgeParser
from app.repository.identity import IdentityRepository
from app.repository.knowledge import KnowledgeRepository
from app.repository.property import PropertyRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from app.service.rbac import RbacService

T = TypeVar("T")
logger = get_logger(__name__)


class KnowledgeService:
    def __init__(
        self,
        session: Session,
        repository: KnowledgeRepository | None = None,
        property_repository: PropertyRepository | None = None,
        audit_service: AuditService | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or KnowledgeRepository(session)
        self._properties = property_repository or PropertyRepository(session)
        self._audit = audit_service or AuditService(session)
        self._principal_loader = principal_loader or AuthService(
            session,
            IdentityRepository(session),
        ).load_principal
        self._settings = settings or get_settings()
        self._parser = TextKnowledgeParser()

    def create_document(
        self,
        *,
        actor: Principal,
        property_id: int,
        title: str,
        document_type: str,
        visibility: str,
        source_text: str,
        request_id: str,
        claimed_tenant_id: int | None = None,
    ) -> tuple[KnowledgeDocument, KnowledgeIngestionJob]:
        return self._execute(
            lambda: self._create_document(
                actor=actor,
                property_id=property_id,
                title=title,
                document_type=document_type,
                visibility=visibility,
                source_text=source_text,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
            )
        )

    def archive_document(
        self,
        *,
        actor: Principal,
        document_id: int,
        request_id: str,
        claimed_tenant_id: int | None = None,
        claimed_property_id: int | None = None,
    ) -> KnowledgeDocument:
        return self._execute(
            lambda: self._archive_document(
                actor=actor,
                document_id=document_id,
                request_id=request_id,
                claimed_tenant_id=claimed_tenant_id,
                claimed_property_id=claimed_property_id,
            )
        )

    def ingest_version(
        self,
        *,
        document_id: int,
        version: int,
        request_id: str,
        rebuild: bool = False,
    ) -> None:
        settings = self._settings
        with transaction_context(self._session):
            job = self._repository.get_ingestion_job_for_update(
                document_id=document_id,
                version=version,
            )
            if job is None:
                return
            if (
                job.status == KnowledgeIngestionStatus.SUCCEEDED.value
                and not rebuild
            ):
                return
            document = self._repository.get_document(document_id)
            version_row = self._repository.get_version(
                document_id=document_id,
                version=version,
            )
            if document is None or version_row is None:
                job.status = KnowledgeIngestionStatus.FAILED.value
                job.error_code = KnowledgeErrorCode.NOT_FOUND
                return
            job.status = KnowledgeIngestionStatus.PROCESSING.value
            job.started_at = utc_now()
            job.attempt = (job.attempt or 0) + 1
            try:
                text = self._parser.parse(
                    filename=version_row.storage_key,
                    content=version_row.source_text,
                )
            except KnowledgeParseError:
                job.status = KnowledgeIngestionStatus.FAILED.value
                job.error_code = KnowledgeErrorCode.INVALID_INPUT
                version_row.status = KnowledgeVersionStatus.FAILED.value
                job.finished_at = utc_now()
                return
            pieces = chunk_text(text, title=document.title)
            if not pieces:
                job.status = KnowledgeIngestionStatus.FAILED.value
                job.error_code = KnowledgeErrorCode.INVALID_INPUT
                version_row.status = KnowledgeVersionStatus.FAILED.value
                job.finished_at = utc_now()
                return
            records = list(
                self._repository.list_chunks_for_version(
                    document_id=document_id,
                    version=version,
                )
            )
            if not records:
                for item in pieces:
                    record = self._repository.add_chunk(
                        KnowledgeChunk(
                            tenant_id=document.tenant_id,
                            property_id=document.property_id,
                            document_id=document.id,
                            version=version_row.version,
                            chunk_no=item.chunk_no,
                            visibility=document.visibility,
                            source_locator=item.source_locator,
                            excerpt=item.text[:500],
                            content_hash=item.content_hash,
                            vector_id=0,
                        )
                    )
                    record.vector_id = record.id
                    records.append(record)
            embedder = build_embedding_provider(settings)
            store = build_milvus_store(settings)
            try:
                vectors = embedder.embed(tuple(item.excerpt for item in records))
            except (ProviderTimeoutError, ProviderUnavailableError) as exc:
                logger.warning(
                    "knowledge_ingest_embedding_failed",
                    document_id=document_id,
                    error_type=type(exc).__name__,
                    request_id=request_id,
                )
                self._fail_ingest(job, version_row, rebuild=rebuild)
                return
            try:
                store.upsert(
                    chunk_ids=[item.id for item in records],
                    tenant_ids=[item.tenant_id for item in records],
                    property_ids=[
                        milvus_property_id(item.property_id) for item in records
                    ],
                    visibilities=[item.visibility for item in records],
                    document_ids=[item.document_id for item in records],
                    document_versions=[item.version for item in records],
                    vectors=vectors,
                    dimensions=settings.embedding_dimensions,
                )
            except ProviderUnavailableError:
                logger.warning(
                    "knowledge_ingest_milvus_failed",
                    document_id=document_id,
                    request_id=request_id,
                )
                self._fail_ingest(job, version_row, rebuild=rebuild)
                return
            self._activate_version(document, version_row, job)

    def _fail_ingest(
        self,
        job: KnowledgeIngestionJob,
        version_row: KnowledgeDocumentVersion,
        *,
        rebuild: bool,
    ) -> None:
        if rebuild and version_row.status == KnowledgeVersionStatus.ACTIVE.value:
            job.status = KnowledgeIngestionStatus.SUCCEEDED.value
            job.error_code = KnowledgeErrorCode.UNAVAILABLE
            job.finished_at = utc_now()
            return
        job.status = KnowledgeIngestionStatus.FAILED.value
        job.error_code = KnowledgeErrorCode.UNAVAILABLE
        version_row.status = KnowledgeVersionStatus.FAILED.value
        job.finished_at = utc_now()

    def _activate_version(
        self,
        document: KnowledgeDocument,
        version_row: KnowledgeDocumentVersion,
        job: KnowledgeIngestionJob,
    ) -> None:
        previous = document.current_version
        if previous is not None and previous != version_row.version:
            old = self._repository.get_version(
                document_id=document.id,
                version=previous,
            )
            if old is not None and old.status == KnowledgeVersionStatus.ACTIVE.value:
                old.status = KnowledgeVersionStatus.SUPERSEDED.value
        version_row.status = KnowledgeVersionStatus.ACTIVE.value
        document.current_version = version_row.version
        document.status = KnowledgeDocumentStatus.ACTIVE.value
        job.status = KnowledgeIngestionStatus.SUCCEEDED.value
        job.finished_at = utc_now()
        job.error_code = None

    def _create_document(
        self,
        *,
        actor: Principal,
        property_id: int,
        title: str,
        document_type: str,
        visibility: str,
        source_text: str,
        request_id: str,
        claimed_tenant_id: int | None,
    ) -> tuple[KnowledgeDocument, KnowledgeIngestionJob]:
        current = self._reload(actor)
        property_record = self._properties.get_property_by_id(property_id)
        if property_record is None:
            raise AppError(
                code=KnowledgeErrorCode.NOT_FOUND,
                message="Property was not found",
                status_code=404,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != property_record.tenant_id
        ):
            raise AppError(
                code=KnowledgeErrorCode.SCOPE_MISMATCH,
                message="Property does not match claimed tenant",
                status_code=422,
            )
        scope = OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=property_record.tenant_id,
            property_id=property_record.id,
        )
        RbacService.require_permission(current, KNOWLEDGE_MANAGE, scope)
        try:
            parsed_type = KnowledgeDocumentType(document_type)
            parsed_visibility = KnowledgeVisibility(visibility)
        except ValueError as exc:
            raise AppError(
                code=KnowledgeErrorCode.INVALID_INPUT,
                message="Document type or visibility is invalid",
                status_code=422,
            ) from exc
        try:
            text = self._parser.parse(filename=None, content=source_text)
        except KnowledgeParseError as exc:
            raise AppError(
                code=KnowledgeErrorCode.INVALID_INPUT,
                message="Source text is invalid",
                status_code=422,
            ) from exc
        document = self._repository.add_document(
            KnowledgeDocument(
                tenant_id=property_record.tenant_id,
                property_id=property_record.id,
                title=title.strip(),
                document_type=parsed_type.value,
                visibility=parsed_visibility.value,
                status=KnowledgeDocumentStatus.DRAFT.value,
                current_version=None,
                created_by_user_id=current.user_id,
            )
        )
        content_hash = hash_content(text)
        version_row = self._repository.add_version(
            KnowledgeDocumentVersion(
                tenant_id=document.tenant_id,
                property_id=document.property_id,
                document_id=document.id,
                version=1,
                source_text=text,
                content_hash=content_hash,
                status=KnowledgeVersionStatus.PENDING.value,
                created_by_user_id=current.user_id,
            )
        )
        job = self._repository.add_job(
            KnowledgeIngestionJob(
                tenant_id=document.tenant_id,
                property_id=document.property_id,
                document_id=document.id,
                version=version_row.version,
                content_hash=content_hash,
                status=KnowledgeIngestionStatus.PENDING.value,
            )
        )
        self._audit.append(
            action=AuditAction.KNOWLEDGE_DOCUMENT_CREATED,
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            actor_user_id=current.user_id,
            resource_id=document.id,
            request_id=request_id,
            scope_type=ScopeType.PROPERTY,
            tenant_id=document.tenant_id,
            property_id=document.property_id,
            details={
                "document_id": document.id,
                "visibility": document.visibility,
                "document_type": document.document_type,
            },
        )
        return document, job

    def _archive_document(
        self,
        *,
        actor: Principal,
        document_id: int,
        request_id: str,
        claimed_tenant_id: int | None,
        claimed_property_id: int | None,
    ) -> KnowledgeDocument:
        current = self._reload(actor)
        document = self._repository.get_document(document_id)
        if document is None:
            raise AppError(
                code=KnowledgeErrorCode.NOT_FOUND,
                message="Knowledge document was not found",
                status_code=404,
            )
        if (
            claimed_tenant_id is not None
            and claimed_tenant_id != document.tenant_id
        ) or (
            claimed_property_id is not None
            and claimed_property_id != document.property_id
        ):
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        if document.property_id is None:
            scope = OrganizationScope(
                ScopeType.TENANT,
                tenant_id=document.tenant_id,
            )
        else:
            scope = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=document.tenant_id,
                property_id=document.property_id,
            )
        RbacService.require_permission(current, KNOWLEDGE_MANAGE, scope)
        document.status = KnowledgeDocumentStatus.ARCHIVED.value
        self._audit.append(
            action=AuditAction.KNOWLEDGE_DOCUMENT_ARCHIVED,
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
            actor_user_id=current.user_id,
            resource_id=document.id,
            request_id=request_id,
            scope_type=scope.scope_type,
            tenant_id=document.tenant_id,
            property_id=document.property_id,
            details={"document_id": document.id, "status": document.status},
        )
        return document

    def _reload(self, actor: Principal) -> Principal:
        if actor.status != "ACTIVE":
            raise AppError(
                code=AuthErrorCode.USER_DISABLED,
                message="User is not active",
                status_code=401,
            )
        return self._principal_loader(actor.user_id)

    def _execute(self, operation: Callable[[], T]) -> T:
        from app.core.database import run_service_transaction

        try:
            return run_service_transaction(self._session, operation)
        except AppError:
            raise
        except IntegrityError as exc:
            raise AppError(
                code=KnowledgeErrorCode.CONFLICT,
                message="Knowledge document conflicted with current data",
                status_code=409,
            ) from exc
        except SQLAlchemyError as exc:
            raise AppError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Knowledge document could not be saved",
                status_code=500,
            ) from exc
