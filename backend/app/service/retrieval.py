"""Citation-based property knowledge assistant. ACL is never delegated to the LLM."""

from dataclasses import dataclass, field
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
import json
from math import isfinite
import re
from time import perf_counter

from sqlalchemy import MetaData, Table, inspect as sa_inspect, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import begin_owned_transaction
from app.core.errors import AppError, IamErrorCode, KnowledgeErrorCode
from app.core.logging import get_logger
from app.core.time import utc_now
from app.domain.ai import AiCallStatus, ProviderType
from app.domain.identity import OrganizationScope, Principal, ScopeType
from app.domain.knowledge import (
    DEFAULT_MIN_RELEVANCE,
    DEFAULT_RERANK_TOP_K,
    DEFAULT_RETRIEVAL_TOP_K,
    INSUFFICIENT_KNOWLEDGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_UNAVAILABLE,
    RAG_ANSWER_PROMPT_VERSION,
    KnowledgeDocumentStatus,
    KnowledgeVisibility,
    RagAnswerOutput,
    RagAnswerStatus,
    hash_content,
    rag_answer_prompt_body,
    milvus_property_id,
    visibilities_for_principal,
)
from app.model.ai import AiCallLog
from app.model.knowledge import KnowledgeChunk, KnowledgeDocument
from app.provider.errors import (
    ProviderError,
    ProviderInvalidOutputError,
    ProviderInvalidOutputError as _ProviderInvalidOutputError,
    ProviderTimeoutError,
)
from app.provider.factory import (
    build_embedding_provider,
    build_rag_llm_provider,
    build_milvus_store,
    build_reranker_provider,
)
from app.provider.usage import valid_token_count
from app.repository.ai import AiRepository
from app.repository.employee import EmployeeRepository
from app.repository.identity import IdentityRepository
from app.repository.knowledge import KnowledgeRepository
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.service.identity.rbac import RbacService
from app.service.identity.auth import AuthService

logger = get_logger(__name__)


def _diagnostic(level: str, event: str, **values) -> None:
    """Best-effort diagnostics never replace business results or exceptions."""
    try:
        getattr(logger, level)(event, **values)
    except Exception:
        pass



@dataclass(frozen=True, slots=True)
class RetrievalAcl:
    tenant_id: int
    property_ids: frozenset[int]
    visibilities: frozenset[str]
    allow_all_properties: bool


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    status: str
    answer: str
    citations: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    id: int
    document_id: int
    tenant_id: int
    property_id: int | None
    visibility: str
    version: int
    excerpt: str
    source_locator: str
    title: str


@dataclass
class _QueryTrace:
    """Request-local, content-free diagnostics; never an authorization cache."""
    phases: list[dict[str, object]] = field(default_factory=list)
    retrieved_count: int = 0
    visible_count: int = 0
    selected_count: int = 0
    rerank_fallback: bool = False
    rerank_fallback_error_code: str | None = None
    reason: str | None = None
    stage: str = "initial_access"

    def call(self, stage, provider, method, *args, **kwargs):
        self.stage = stage
        started = perf_counter()
        model = getattr(provider, "model_version", None)
        model = model if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,128}", model) and "://" not in model else "unknown"
        phase = dict(stage=stage, status="failed", model_version=model,
                     token_usage=None, error_code=None)
        try:
            result = method(*args, **kwargs)
            phase["status"] = "success"
            phase["token_usage"] = valid_token_count(getattr(result, "token_usage", None))
            return result
        except ProviderError as exc:
            phase["error_code"] = exc.error_code
            raise
        except AppError as exc:
            phase["error_code"] = exc.code
            raise
        except Exception:
            phase["error_code"] = "SYS.INTERNAL_ERROR"
            raise
        finally:
            phase["latency_ms"] = round(max(0, perf_counter() - started) * 1000, 3)
            self.phases.append(phase)


KNOWLEDGE_CALL_LOG_BIZ_TYPE = "KNOWLEDGE_QUERY"

_UNSALTED_AUDIT_HASH_WARNED = False


def _status_for_error(error: ProviderError) -> AiCallStatus:
    """Same mapping the work-order chain uses, so both audits read alike."""

    if isinstance(error, ProviderTimeoutError):
        return AiCallStatus.TIMEOUT
    if isinstance(error, _ProviderInvalidOutputError):
        return AiCallStatus.INVALID
    return AiCallStatus.FAILED


class RetrievalService:
    def __init__(
        self,
        session: Session,
        repository: KnowledgeRepository | None = None,
        property_repository: PropertyRepository | None = None,
        resident_repository: ResidentBindingRepository | None = None,
        employee_repository: EmployeeRepository | None = None,
        settings: Settings | None = None,
        principal_loader: Callable[[int], Principal] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or KnowledgeRepository(session)
        self._properties = property_repository or PropertyRepository(session)
        self._residents = resident_repository or ResidentBindingRepository(session)
        self._employees = employee_repository or EmployeeRepository(session)
        self._settings = settings or get_settings()
        self._ai = AiRepository(session)
        self._principal_loader = principal_loader or AuthService(
            session, IdentityRepository(session),
        ).load_principal
        self._warn_if_audit_hashes_are_unsalted()

    def query(
        self,
        *,
        actor: Principal,
        question: str,
        request_id: str,
        property_id: int | None = None,
        claimed_tenant_id: int | None = None,
        audience: str = "resident",
    ) -> AssistantAnswer:
        trace = _QueryTrace()
        started = perf_counter()
        status, error_code, citation_count = "error", None, 0
        try:
            answer = self._query(actor=actor, question=question, request_id=request_id,
                                 property_id=property_id, claimed_tenant_id=claimed_tenant_id,
                                 audience=audience, trace=trace)
            status, citation_count = answer.status, len(answer.citations)
            return answer
        except AppError as exc:
            status, error_code = "rejected", exc.code
            raise
        except Exception:
            error_code = "SYS.INTERNAL_ERROR"
            raise
        finally:
            _diagnostic(
                "info", "knowledge_query_completed", request_id=request_id,
                audience=audience if audience in {"resident", "property"} else "invalid",
                prompt_version=self._settings.rag_answer_prompt_version,
                status=status, error_code=error_code, reason=trace.reason, stage=trace.stage,
                latency_ms=round(max(0, perf_counter() - started) * 1000, 3),
                retrieved_count=trace.retrieved_count, visible_count=trace.visible_count,
                selected_count=trace.selected_count, citation_count=citation_count,
                rerank_fallback=trace.rerank_fallback,
                rerank_fallback_error_code=trace.rerank_fallback_error_code, phases=trace.phases,
            )

    def _audited_call(
        self,
        *,
        trace: _QueryTrace,
        stage: str,
        provider: object,
        method: Callable[..., object],
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        provider_type: ProviderType,
        operation: str,
        request_id: str,
        tenant_id: int | None,
        input_hash: str,
    ) -> object:
        """Run one provider call and audit it, success or failure (F2 / t177).

        The row is written in its own transaction: a failed provider must still
        leave a row, and an audit write must never change the answer.
        """

        started_at = utc_now()
        try:
            result = trace.call(stage, provider, method, *args, **(kwargs or {}))
        except ProviderError as exc:
            self._record_call(
                provider_type=provider_type, operation=operation, request_id=request_id,
                tenant_id=tenant_id, input_hash=input_hash, started_at=started_at,
                phase=trace.phases[-1], status=_status_for_error(exc),
                error_code=exc.error_code,
            )
            raise
        self._record_call(
            provider_type=provider_type, operation=operation, request_id=request_id,
            tenant_id=tenant_id, input_hash=input_hash, started_at=started_at,
            phase=trace.phases[-1], status=AiCallStatus.SUCCESS, error_code=None,
        )
        return result

    def _record_call(
        self,
        *,
        provider_type: ProviderType,
        operation: str,
        request_id: str,
        tenant_id: int | None,
        input_hash: str,
        started_at,
        phase: dict[str, object],
        status: AiCallStatus,
        error_code: str | None,
    ) -> None:
        latency_ms = int(max(0.0, float(phase.get("latency_ms") or 0.0)))
        model_version = str(phase.get("model_version") or "unknown")[:64]
        try:
            with begin_owned_transaction(self._session):
                record = self._ai.add_call_log(
                    AiCallLog(
                        tenant_id=tenant_id,
                        property_id=None,
                        work_order_id=None,
                        request_id=request_id[:128],
                        provider_type=provider_type.value,
                        operation=operation,
                        biz_type=KNOWLEDGE_CALL_LOG_BIZ_TYPE,
                        prompt_version=None,
                        model_version=model_version,
                        status=status.value,
                        started_at=started_at,
                        finished_at=started_at + timedelta(milliseconds=latency_ms),
                        latency_ms=latency_ms,
                        attempt=1,
                        error_code=error_code,
                        token_usage=None,
                        input_hash=input_hash,
                    )
                )
                self._record_provider_mode(record.id, provider_type)
        except Exception:  # audit must never replace the business result
            _diagnostic(
                "warning", "knowledge_call_log_failed", request_id=request_id,
                provider_type=provider_type.value, operation=operation,
            )

    def _record_provider_mode(self, call_log_id: int, provider_type: ProviderType) -> None:
        """Fill the 0009 provenance column when the shared schema has it."""

        bind = self._session.get_bind()
        if bind is None:
            return
        columns = {column["name"] for column in sa_inspect(bind).get_columns("ai_call_logs")}
        if "provider_mode" not in columns:
            return
        configured = {
            ProviderType.EMBEDDING: self._settings.embedding_provider,
            ProviderType.RERANK: self._settings.reranker_provider,
        }[provider_type]
        table = Table("ai_call_logs", MetaData(), autoload_with=bind)
        self._session.execute(
            update(table)
            .where(table.c.id == call_log_id)
            .values(provider_mode=str(configured).upper())
        )

    def _query(
        self, *, actor: Principal, question: str, request_id: str,
        property_id: int | None, claimed_tenant_id: int | None,
        audience: str, trace: _QueryTrace,
    ) -> AssistantAnswer:
        if actor.status != "ACTIVE":
            raise AppError(
                code=IamErrorCode.FORBIDDEN,
                message="Permission denied",
                status_code=403,
            )
        read_context = dict(actor=actor, property_id=property_id, claimed_tenant_id=claimed_tenant_id, audience=audience)
        with self._read_phase(**read_context) as acl:
            initial_acl = acl
        settings = self._settings
        embedder = build_embedding_provider(settings)
        store = build_milvus_store(settings)
        reranker = build_reranker_provider(settings)
        llm = build_rag_llm_provider(settings)
        try:
            query_vector = self._audited_call(
                trace=trace, stage="embed", provider=embedder, method=embedder.embed,
                args=((question,),), provider_type=ProviderType.EMBEDDING,
                operation="embed", request_id=request_id,
                tenant_id=initial_acl.tenant_id, input_hash=self._audit_hash(question),
            )[0]
            hits = trace.call("search", store, store.search,
                vector=query_vector,
                expr=self._milvus_expr(initial_acl),
                top_k=DEFAULT_RETRIEVAL_TOP_K,
                dimensions=settings.embedding_dimensions,
            )
        except ProviderError:
            trace.reason = "retrieval_unavailable"
            _diagnostic("warning", "knowledge_retrieval_unavailable", request_id=request_id)
            return AssistantAnswer(
                status=KNOWLEDGE_UNAVAILABLE,
                answer="知识助手暂时不可用",
                citations=(),
            )
        chunk_ids = tuple(int(hit["chunk_id"]) for hit in hits)
        trace.retrieved_count = len(chunk_ids)
        trace.stage = "evidence_access"
        with self._read_phase(**read_context) as acl:
            visible = self._snapshots(self._repository.get_chunks_by_ids(chunk_ids), acl)
        trace.visible_count = len(visible)
        if not visible:
            trace.reason = "no_visible_evidence"
            return AssistantAnswer(
                status=INSUFFICIENT_KNOWLEDGE,
                answer="",
                citations=(),
            )
        try:
            scores = self._audited_call(
                trace=trace, stage="rerank", provider=reranker, method=reranker.rerank,
                kwargs={
                    "query": question,
                    "documents": tuple(item.excerpt for item in visible),
                },
                provider_type=ProviderType.RERANK, operation="rerank",
                request_id=request_id, tenant_id=initial_acl.tenant_id,
                # Evidence ids, not evidence text, identify what was resorted.
                input_hash=self._audit_hash(
                    question + "\x00" + ",".join(str(item.id) for item in visible)
                ),
            )
            try:
                valid_scores = (
                    isinstance(scores, (tuple, list))
                    and len(scores) == len(visible)
                    and all(
                        not isinstance(score, bool)
                        and isinstance(score, (int, float))
                        and isfinite(score)
                        for score in scores
                    )
                )
            except OverflowError:
                valid_scores = False
            if not valid_scores:
                raise ProviderInvalidOutputError("Reranker returned invalid scores")
            ranked = sorted(
                zip(visible, scores, strict=True),
                key=lambda pair: pair[1],
                reverse=True,
            )
            selected = tuple(
                item
                for item, score in ranked[:DEFAULT_RERANK_TOP_K]
                if score >= DEFAULT_MIN_RELEVANCE
            )
        except ProviderError as exc:
            trace.rerank_fallback = True
            trace.rerank_fallback_error_code = exc.error_code
            if trace.phases[-1]["status"] == "success":
                trace.phases[-1].update(status="invalid_output", error_code=exc.error_code)
            _diagnostic("warning", "knowledge_rerank_fallback", request_id=request_id, error_code=exc.error_code)
            # Preserve the established vector-order fallback, with ACL already
            # applied. These are unscored candidates, not synthetic 1.0 matches.
            selected = visible[:DEFAULT_RERANK_TOP_K]
        trace.selected_count = len(selected)
        if not selected:
            trace.reason = "below_relevance"
            return AssistantAnswer(
                status=INSUFFICIENT_KNOWLEDGE,
                answer="",
                citations=(),
            )
        allowed_ids = {item.id for item in selected}
        trace.stage = "pre_generation_access"
        with self._read_phase(**read_context) as acl:
            if not self._evidence_is_current(selected, acl):
                trace.reason = "evidence_changed_before_generation"
                _diagnostic("warning", "knowledge_evidence_changed", request_id=request_id, stage="before_generation")
                return AssistantAnswer(status=KNOWLEDGE_UNAVAILABLE, answer="知识助手暂时不可用", citations=())
        prompt = self._prompt(
            question, selected, prompt_version=self._settings.rag_answer_prompt_version
        )
        try:
            raw = trace.call("generate", llm, llm.structured_completion,
                prompt=prompt,
                prompt_version=self._settings.rag_answer_prompt_version,
            )
            parsed = RagAnswerOutput.model_validate(raw)
        except ProviderError:
            trace.reason = "generation_unavailable"
            return AssistantAnswer(status=KNOWLEDGE_UNAVAILABLE, answer="知识助手暂时不可用", citations=())
        except ValueError:
            trace.reason = "invalid_generation"
            trace.phases[-1].update(status="invalid_output", error_code="AI.INVALID_OUTPUT")
            return AssistantAnswer(
                status=KNOWLEDGE_UNAVAILABLE,
                answer="知识助手暂时不可用",
                citations=(),
            )
        if parsed.status not in {s.value for s in RagAnswerStatus} or (
            parsed.status == RagAnswerStatus.ANSWERED.value and not parsed.answer.strip()
        ):
            trace.reason = "invalid_generation"
            return AssistantAnswer(status=KNOWLEDGE_UNAVAILABLE,answer="知识助手暂时不可用",citations=())
        if parsed.status == INSUFFICIENT_KNOWLEDGE:
            trace.reason = "model_insufficient_evidence"
            return AssistantAnswer(status=INSUFFICIENT_KNOWLEDGE, answer="", citations=())
        if parsed.status == KNOWLEDGE_UNAVAILABLE or any(
            item not in allowed_ids for item in parsed.citation_chunk_ids
        ):
            trace.reason = "model_unavailable" if parsed.status == KNOWLEDGE_UNAVAILABLE else "invalid_citation"
            return AssistantAnswer(
                status=KNOWLEDGE_UNAVAILABLE,
                answer="知识助手暂时不可用",
                citations=(),
            )
        cited_ids = set(parsed.citation_chunk_ids)
        cited = tuple(item for item in selected if item.id in cited_ids)
        if not cited:
            trace.reason = "no_citation"
            return AssistantAnswer(
                status=INSUFFICIENT_KNOWLEDGE,
                answer="",
                citations=(),
            )
        trace.stage = "pre_response_access"
        with self._read_phase(**read_context) as acl:
            if not self._evidence_is_current(selected, acl):
                trace.reason = "evidence_changed_before_response"
                _diagnostic("warning", "knowledge_evidence_changed", request_id=request_id, stage="before_response")
                return AssistantAnswer(status=KNOWLEDGE_UNAVAILABLE, answer="知识助手暂时不可用", citations=())
        return AssistantAnswer(
            status=RagAnswerStatus.ANSWERED.value,
            answer=parsed.answer,
            citations=self._citations(cited),
        )

    @contextmanager
    def _read_phase(
        self, *, actor: Principal, property_id: int | None, claimed_tenant_id: int | None,
        audience: str = "resident",
    ) -> Iterator[RetrievalAcl]:
        with begin_owned_transaction(self._session):
            # The factory uses expire_on_commit=False. A new transaction alone
            # would still permit stale ORM identity-map entries to be reused.
            self._session.expire_all()
            current = self._principal_loader(actor.user_id)
            if current.status != "ACTIVE":
                raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
            yield self._acl_for(actor=current, property_id=property_id, claimed_tenant_id=claimed_tenant_id, audience=audience)

    def _snapshots(self, chunks: tuple[KnowledgeChunk, ...], acl: RetrievalAcl) -> tuple[EvidenceSnapshot, ...]:
        result = []
        for chunk in chunks:
            if self._mysql_visible(chunk, acl):
                document = self._session.get(KnowledgeDocument, chunk.document_id)
                result.append(EvidenceSnapshot(
                    id=chunk.id, document_id=chunk.document_id, tenant_id=chunk.tenant_id,
                    property_id=chunk.property_id, visibility=chunk.visibility, version=chunk.version,
                    excerpt=chunk.excerpt, source_locator=chunk.source_locator, title=document.title,
                ))
        return tuple(result)

    def _evidence_is_current(self, selected: tuple[EvidenceSnapshot, ...], acl: RetrievalAcl) -> bool:
        chunks = self._repository.get_chunks_by_ids(tuple(item.id for item in selected))
        return self._snapshots(chunks, acl) == selected

    def _acl_for(
        self,
        *,
        actor: Principal,
        property_id: int | None,
        claimed_tenant_id: int | None,
        audience: str = "resident",
    ) -> RetrievalAcl:
        if audience not in {"resident", "property"}:
            raise AppError(code=KnowledgeErrorCode.INVALID_INPUT, message="Invalid audience", status_code=422)
        if property_id is not None:
            property_record = self._properties.get_property_by_id(property_id)
            if property_record is None:
                raise AppError(
                    code=KnowledgeErrorCode.NOT_FOUND,
                    message="Property was not found",
                    status_code=404,
                )
            target = OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=property_record.tenant_id,
                property_id=property_id,
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
            RbacService.require_permission(actor, KNOWLEDGE_READ, target)
            visibilities = self._visibilities_for_target(
                actor=actor, target=target, audience=audience,
            )
            return RetrievalAcl(
                tenant_id=property_record.tenant_id,
                property_ids=frozenset({property_id, 0}),
                visibilities=frozenset(item.value for item in visibilities),
                allow_all_properties=False,
            )
        tenant_ids = {
            grant.scope.tenant_id for grant in actor.grants
            if KNOWLEDGE_READ in grant.permissions
            and grant.role_code == "TENANT_ADMIN"
            and grant.scope.scope_type is ScopeType.TENANT
        }
        if len(tenant_ids) != 1 and claimed_tenant_id is None:
            raise AppError(
                code=KnowledgeErrorCode.INVALID_INPUT,
                message="property_id is required",
                status_code=422,
            )
        tenant_id = claimed_tenant_id if claimed_tenant_id is not None else next(iter(tenant_ids))
        scope = OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id)
        RbacService.require_permission(actor, KNOWLEDGE_READ, scope)
        visibilities = self._visibilities_for_target(
            actor=actor, target=scope, audience=audience,
        )
        return RetrievalAcl(
            tenant_id=tenant_id,
            property_ids=frozenset({0}),
            visibilities=frozenset(item.value for item in visibilities),
            allow_all_properties=True,
        )

    def _visibilities_for_target(
        self,
        *,
        actor: Principal,
        target: OrganizationScope,
        audience: str,
    ) -> frozenset[KnowledgeVisibility]:
        matching_roles = {
            grant.role_code
            for grant in actor.grants
            if KNOWLEDGE_READ in grant.permissions and grant.scope.allows(target)
        }
        tenant_scope = OrganizationScope(
            ScopeType.TENANT,
            tenant_id=target.tenant_id,
        )
        is_tenant_admin = any(
            grant.role_code == "TENANT_ADMIN"
            and KNOWLEDGE_READ in grant.permissions
            and grant.scope.allows(tenant_scope)
            for grant in actor.grants
        )
        is_system_admin = any(
            grant.role_code == "SYSTEM_ADMIN"
            and grant.scope.scope_type is ScopeType.SYSTEM
            and KNOWLEDGE_READ in grant.permissions
            for grant in actor.grants
        )
        property_id = target.property_id
        now = utc_now()
        is_manager = property_id is not None and bool(matching_roles & {"SUPERVISOR", "PROPERTY_ADMIN"})
        is_resident = property_id is not None and "RESIDENT" in matching_roles and self._residents.has_active_property_relation(
            user_id=actor.user_id, tenant_id=target.tenant_id, property_id=property_id, now=now,
        )
        is_staff = False
        if property_id is not None and "STAFF" in matching_roles:
            employee = self._employees.get_employee_for_user(actor.user_id)
            is_staff = bool(
                employee is not None and employee.status == "ACTIVE"
                and employee.tenant_id == target.tenant_id
                and property_id in self._employees.list_active_property_ids_for_employee(employee_id=employee.id, now=now)
            )
        can_read_internal = is_staff or is_manager or is_tenant_admin or is_system_admin
        if not (can_read_internal or is_resident) or (audience == "property" and not can_read_internal):
            raise AppError(code=IamErrorCode.FORBIDDEN, message="Permission denied", status_code=403)
        if audience == "resident":
            return frozenset({KnowledgeVisibility.RESIDENT})
        return visibilities_for_principal(
            is_resident=is_resident,
            is_staff=is_staff,
            is_manager=is_manager,
            is_tenant_admin=is_tenant_admin,
            is_system_admin=is_system_admin,
        )

    @staticmethod
    def _milvus_expr(acl: RetrievalAcl) -> str:
        vis = ", ".join(f'"{item}"' for item in sorted(acl.visibilities))
        if acl.allow_all_properties:
            return f"tenant_id == {acl.tenant_id} && visibility in [{vis}]"
        props = ", ".join(str(item) for item in sorted(acl.property_ids))
        return (
            f"tenant_id == {acl.tenant_id} && property_id in [{props}] "
            f"&& visibility in [{vis}]"
        )

    def _mysql_visible(self, chunk: KnowledgeChunk, acl: RetrievalAcl) -> bool:
        if chunk.tenant_id != acl.tenant_id:
            return False
        if chunk.visibility not in acl.visibilities:
            return False
        milvus_pid = milvus_property_id(chunk.property_id)
        if not acl.allow_all_properties and milvus_pid not in acl.property_ids:
            return False
        document = self._session.get(KnowledgeDocument, chunk.document_id)
        if document is None:
            return False
        if document.status != KnowledgeDocumentStatus.ACTIVE.value:
            return False
        if document.current_version != chunk.version:
            return False
        if document.visibility != chunk.visibility:
            return False
        if document.tenant_id != chunk.tenant_id:
            return False
        if document.property_id != chunk.property_id:
            return False
        return True

    def _warn_if_audit_hashes_are_unsalted(self) -> None:
        """t187-2: production should set AUDIT_HASH_SALT; say so once."""

        global _UNSALTED_AUDIT_HASH_WARNED
        raw = getattr(self._settings, "audit_hash_salt", "")
        salt = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw or "")
        app_env = getattr(self._settings, "app_env", "")
        if salt or app_env != "production" or _UNSALTED_AUDIT_HASH_WARNED:
            return
        _UNSALTED_AUDIT_HASH_WARNED = True
        logger.warning(
            "audit_hash_unsalted",
            detail="AUDIT_HASH_SALT is empty; audit input_hash values cannot be"
            " rotated and a dictionary attack on short questions stays possible",
        )

    def _audit_hash(self, value: str) -> str:
        """Hash audit payloads with the configured salt.

        t187-2: an unsalted sha256 of a short question is reversible from a
        dictionary, and identical questions link across tenants. The salt is a
        deployment secret (AUDIT_HASH_SALT); when it is empty the hash is still
        not bare sha256, so a pre-built dictionary cannot be replayed, and a
        production stack without a salt says so once at startup.
        """

        raw = getattr(self._settings, "audit_hash_salt", "")
        salt = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw or "")
        return hash_content(f"{salt}\x00{value}")

    @staticmethod
    def _prompt(
        question: str,
        chunks: tuple[EvidenceSnapshot, ...],
        *,
        prompt_version: str,
    ) -> str:
        evidence = "\n".join(
            f"chunk-id:{item.id} locator:{item.source_locator}\n{item.excerpt}"
            for item in chunks
        )
        # t188: the body comes from the version we name, so the audit row's
        # prompt_version always describes the text that actually ran.
        return (
            f"{rag_answer_prompt_body(prompt_version)}\n\n"
            f"Allowed citation_chunk_ids: {json.dumps([item.id for item in chunks])}. "
            "Copy these exact database chunk IDs for supporting evidence; they are "
            "not document positions or the chunk numbers inside locators. "
            "Never renumber them starting from 1.\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"Question: {question}"
        )

    def _citations(
        self,
        chunks: tuple[EvidenceSnapshot, ...],
    ) -> tuple[dict[str, object], ...]:
        citations: list[dict[str, object]] = []
        for chunk in chunks:
            citations.append(
                {
                    "document_id": chunk.document_id,
                    "title": chunk.title,
                    "version": chunk.version,
                    "source_locator": chunk.source_locator,
                    "excerpt": chunk.excerpt,
                }
            )
        return tuple(citations)
