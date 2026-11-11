"""Atomic notification claim and knowledge ingest attempt/version guards."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import begin_owned_transaction
from app.core.errors import KnowledgeErrorCode, NotificationErrorCode
from app.domain.knowledge import (
    KnowledgeDocumentStatus,
    KnowledgeIngestionStatus,
    KnowledgeVersionStatus,
    hash_content,
)
from app.model.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    NotificationDelivery,
)
from app.provider.embedding import DevelopmentEmbeddingProvider
from app.provider.notification import NotificationProvider
from app.repository.identity import IdentityRepository
from app.repository.knowledge import KnowledgeRepository
from app.service.identity.auth import AuthService
from app.service.knowledge import KnowledgeService
from app.service.notification import NotificationService
from tests.integration.conftest import commit_open_transaction, fetch_independent
from tests.integration.work_order_test_data import create_work_order_graph

COLLECTION = f"d4_claim_{uuid4().hex[:8]}"


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "dev_auth_enabled": True,
        "milvus_collection": COLLECTION,
        "embedding_dimensions": 8,
        "ai_enqueue_on_create": False,
    }
    values.update(overrides)
    return Settings(**values)


def _principal(session: Session, user_id: int):
    principal = AuthService(session, IdentityRepository(session)).load_principal(user_id)
    commit_open_transaction(session)
    return principal


def _open_session(session: Session) -> Session:
    return Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)


def test_concurrent_deliver_pending_calls_provider_once(
    identity_session: Session,
    monkeypatch,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    NotificationService(identity_session, settings=settings).enqueue(
        tenant_id=graph.employees.tenant_1_id,
        property_id=graph.employees.property_1_id,
        user_id=graph.employees.resident_id,
        title="claim-once",
        body="body",
        resource_type="WORK_ORDER",
        resource_id=1,
    )
    identity_session.commit()
    delivery_id = identity_session.scalar(select(NotificationDelivery.id))
    assert delivery_id is not None
    commit_open_transaction(identity_session)

    started = Event()
    release = Event()
    calls: list[int] = []
    lock = Lock()

    class GateProvider(NotificationProvider):
        def deliver_push(self, *, user_id: int, title: str, body: str) -> None:
            del user_id, title, body
            with lock:
                calls.append(1)
            started.set()
            assert release.wait(15)

    monkeypatch.setattr(
        "app.service.notification.build_notification_provider",
        lambda _settings: GateProvider(),
    )

    def worker() -> str:
        session = _open_session(identity_session)
        try:
            return NotificationService(session, settings=settings).deliver_pending(
                delivery_id
            )
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        assert started.wait(15)
        release.set()
        outcomes = {first.result(timeout=15), second.result(timeout=15)}
    assert len(calls) == 1
    assert "SUCCEEDED" in outcomes
    assert "SKIPPED" in outcomes or "SUCCEEDED" in outcomes
    persisted = fetch_independent(identity_session, NotificationDelivery, delivery_id)
    assert persisted is not None
    assert persisted.status == "SUCCEEDED"
    assert persisted.attempt == 1


def test_notification_provider_failure_and_uncertain_are_distinct(
    identity_session: Session,
    monkeypatch,
) -> None:
    graph = create_work_order_graph(identity_session)
    failed_settings = _settings(notification_provider_behavior="unavailable")
    NotificationService(identity_session, settings=failed_settings).enqueue(
        tenant_id=graph.employees.tenant_1_id,
        property_id=graph.employees.property_1_id,
        user_id=graph.employees.resident_id,
        title="fail",
        body="body",
        resource_type="WORK_ORDER",
        resource_id=2,
    )
    NotificationService(identity_session, settings=failed_settings).enqueue(
        tenant_id=graph.employees.tenant_1_id,
        property_id=graph.employees.property_1_id,
        user_id=graph.employees.resident_id,
        title="uncertain",
        body="body",
        resource_type="WORK_ORDER",
        resource_id=3,
    )
    identity_session.commit()
    deliveries = identity_session.scalars(
        select(NotificationDelivery).order_by(NotificationDelivery.id)
    ).all()
    fail_id, uncertain_id = deliveries[-2].id, deliveries[-1].id
    commit_open_transaction(identity_session)

    failed = NotificationService(identity_session, settings=failed_settings).deliver_pending(
        fail_id
    )
    assert failed == "RETRY"
    persisted_fail = fetch_independent(identity_session, NotificationDelivery, fail_id)
    assert persisted_fail is not None
    assert persisted_fail.status == "PENDING"
    assert persisted_fail.attempt == 1
    assert persisted_fail.next_retry_at is not None
    assert persisted_fail.last_error_code == NotificationErrorCode.PROVIDER_FAILED

    class BoomProvider(NotificationProvider):
        def deliver_push(self, *, user_id: int, title: str, body: str) -> None:
            del user_id, title, body
            raise RuntimeError("socket closed after write")

    monkeypatch.setattr(
        "app.service.notification.build_notification_provider",
        lambda _settings: BoomProvider(),
    )
    uncertain = NotificationService(identity_session, settings=_settings()).deliver_pending(
        uncertain_id
    )
    assert uncertain == "UNCERTAIN"
    persisted_uncertain = fetch_independent(
        identity_session, NotificationDelivery, uncertain_id
    )
    assert persisted_uncertain is not None
    assert persisted_uncertain.status == "PENDING"
    assert persisted_uncertain.last_error_code == NotificationErrorCode.PROVIDER_UNCERTAIN


def test_concurrent_ingest_does_not_double_embed(
    identity_session: Session,
    monkeypatch,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    knowledge = KnowledgeService(identity_session, settings=settings)
    document, job = knowledge.create_document(
        actor=_principal(identity_session, graph.employees.manager_p1_id),
        property_id=graph.employees.property_1_id,
        title="领取保护",
        document_type="RULE",
        visibility="RESIDENT",
        source_text="小区电梯夜间检修时间为 23:00 至 05:00。",
        request_id="d4-claim-doc",
    )
    started = Event()
    release = Event()
    calls: list[int] = []
    lock = Lock()

    class GateEmbed(DevelopmentEmbeddingProvider):
        def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
            with lock:
                calls.append(1)
            started.set()
            assert release.wait(20)
            return super().embed(texts)

    monkeypatch.setattr(
        "app.service.knowledge.build_embedding_provider",
        lambda _settings: GateEmbed(dimensions=settings.embedding_dimensions),
    )

    def worker() -> str:
        session = _open_session(identity_session)
        try:
            return KnowledgeService(session, settings=settings).ingest_version(
                document_id=document.id,
                version=job.version,
                request_id=f"d4-claim-ingest-{uuid4().hex[:8]}",
            )
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        second = pool.submit(worker)
        assert started.wait(20)
        release.set()
        outcomes = {first.result(timeout=30), second.result(timeout=30)}
    assert len(calls) == 1
    assert KnowledgeIngestionStatus.SUCCEEDED.value in outcomes
    persisted = fetch_independent(identity_session, KnowledgeDocument, document.id)
    assert persisted is not None
    assert persisted.status == KnowledgeDocumentStatus.ACTIVE.value
    assert persisted.current_version == job.version


def test_stale_ingest_failure_does_not_override_success(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    knowledge = KnowledgeService(identity_session, settings=settings)
    document, job = knowledge.create_document(
        actor=_principal(identity_session, graph.employees.manager_p1_id),
        property_id=graph.employees.property_1_id,
        title="旧失败不得覆盖",
        document_type="FAQ",
        visibility="RESIDENT",
        source_text="报修后一个工作日内响应。",
        request_id="d4-stale-fail-doc",
    )
    knowledge.ingest_version(
        document_id=document.id,
        version=job.version,
        request_id="d4-stale-fail-ingest",
    )
    commit_open_transaction(identity_session)
    current = identity_session.get(KnowledgeIngestionJob, job.id)
    assert current is not None
    assert current.status == KnowledgeIngestionStatus.SUCCEEDED.value
    stale_attempt = max(int(current.attempt) - 1, 0)
    commit_open_transaction(identity_session)
    knowledge._finish_ingest_failure(
        document_id=document.id,
        version=job.version,
        rebuild=False,
        claimed_attempt=stale_attempt,
        claimed_version=job.version,
    )
    persisted = fetch_independent(identity_session, KnowledgeIngestionJob, job.id)
    assert persisted is not None
    assert persisted.status == KnowledgeIngestionStatus.SUCCEEDED.value
    persisted_doc = fetch_independent(identity_session, KnowledgeDocument, document.id)
    assert persisted_doc is not None
    assert persisted_doc.status == KnowledgeDocumentStatus.ACTIVE.value


def test_old_version_cannot_overwrite_newer_active_version(
    identity_session: Session,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    knowledge = KnowledgeService(identity_session, settings=settings)
    document, job = knowledge.create_document(
        actor=_principal(identity_session, graph.employees.manager_p1_id),
        property_id=graph.employees.property_1_id,
        title="版本保护",
        document_type="RULE",
        visibility="RESIDENT",
        source_text="第一版内容。",
        request_id="d4-ver-1",
    )
    knowledge.ingest_version(
        document_id=document.id,
        version=job.version,
        request_id="d4-ver-1-ingest",
    )
    commit_open_transaction(identity_session)
    repository = KnowledgeRepository(identity_session)
    with begin_owned_transaction(identity_session):
        text = "第二版内容，夜间禁止装修。"
        version_two = repository.add_version(
            KnowledgeDocumentVersion(
                tenant_id=document.tenant_id,
                property_id=document.property_id,
                document_id=document.id,
                version=2,
                source_text=text,
                content_hash=hash_content(text),
                status=KnowledgeVersionStatus.PENDING.value,
                created_by_user_id=graph.employees.manager_p1_id,
            )
        )
        repository.add_job(
            KnowledgeIngestionJob(
                tenant_id=document.tenant_id,
                property_id=document.property_id,
                document_id=document.id,
                version=version_two.version,
                content_hash=version_two.content_hash,
                status=KnowledgeIngestionStatus.PENDING.value,
            )
        )
    knowledge.ingest_version(
        document_id=document.id,
        version=2,
        request_id="d4-ver-2-ingest",
    )
    commit_open_transaction(identity_session)
    with begin_owned_transaction(identity_session):
        first_job = repository.get_ingestion_job_for_update(
            document_id=document.id,
            version=1,
        )
        assert first_job is not None
        first_job.status = KnowledgeIngestionStatus.PROCESSING.value
        first_job.attempt = 9
    knowledge._finish_ingest_success(
        document_id=document.id,
        version=1,
        claimed_attempt=9,
        claimed_version=1,
        request_id="d4-stale-version-finish",
    )
    persisted = fetch_independent(identity_session, KnowledgeDocument, document.id)
    assert persisted is not None
    assert persisted.current_version == 2
    assert persisted.status == KnowledgeDocumentStatus.ACTIVE.value
    first_after = fetch_independent(identity_session, KnowledgeIngestionJob, job.id)
    assert first_after is not None
    assert first_after.status == KnowledgeIngestionStatus.FAILED.value
    assert first_after.error_code == KnowledgeErrorCode.CONFLICT


def test_archive_during_embed_does_not_reactivate(
    identity_session: Session,
    monkeypatch,
) -> None:
    graph = create_work_order_graph(identity_session)
    settings = _settings()
    knowledge = KnowledgeService(identity_session, settings=settings)
    document, job = knowledge.create_document(
        actor=_principal(identity_session, graph.employees.manager_p1_id),
        property_id=graph.employees.property_1_id,
        title="归档窗口",
        document_type="SOP",
        visibility="STAFF_ONLY",
        source_text="内部排班不得对住户公开。",
        request_id="d4-arch-doc",
    )
    started = Event()
    release = Event()

    class GateEmbed(DevelopmentEmbeddingProvider):
        def embed(self, texts: Sequence[str]) -> tuple[list[float], ...]:
            started.set()
            assert release.wait(20)
            return super().embed(texts)

    monkeypatch.setattr(
        "app.service.knowledge.build_embedding_provider",
        lambda _settings: GateEmbed(dimensions=settings.embedding_dimensions),
    )

    def worker() -> str:
        session = _open_session(identity_session)
        try:
            return KnowledgeService(session, settings=settings).ingest_version(
                document_id=document.id,
                version=job.version,
                request_id="d4-arch-ingest",
            )
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        assert started.wait(20)
        archive_session = _open_session(identity_session)
        try:
            KnowledgeService(archive_session, settings=settings).archive_document(
                actor=_principal(archive_session, graph.employees.manager_p1_id),
                document_id=document.id,
                request_id="d4-arch-now",
            )
        finally:
            archive_session.close()
        release.set()
        outcome = future.result(timeout=30)
    assert outcome == KnowledgeIngestionStatus.FAILED.value
    persisted = fetch_independent(identity_session, KnowledgeDocument, document.id)
    assert persisted is not None
    assert persisted.status == KnowledgeDocumentStatus.ARCHIVED.value
    job_row = fetch_independent(identity_session, KnowledgeIngestionJob, job.id)
    assert job_row is not None
    assert job_row.error_code == KnowledgeErrorCode.ARCHIVED
