"""Owned vs composing transaction contract: no autobegin join, no silent discard."""

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.database import (
    TransactionContractError,
    begin_owned_transaction,
)
from app.model.identity import User
from app.model.resident import ResidentBindRequest, ResidentHouseRelation
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.resident import ResidentBindingService
from tests.integration.conftest import count_independent, fetch_independent
from tests.integration.resident_test_data import create_resident_binding_graph


def _principal(session: Session, user_id: int):
    auth_session = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        return AuthService(auth_session, IdentityRepository(auth_session)).load_principal(
            user_id
        )
    finally:
        auth_session.close()


def test_public_create_refuses_unexpected_open_transaction_without_discarding_caller(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    marker_id = 0
    with identity_session.begin():
        marker = User(status="ACTIVE", display_name="tx-refuse-marker")
        identity_session.add(marker)
        identity_session.flush()
        marker_id = marker.id
        with pytest.raises(TransactionContractError, match="unexpected open transaction"):
            ResidentBindingService(identity_session).create_bind_request(
                actor=principal,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                request_id="d4-tx-refuse",
            )
        assert identity_session.get(User, marker_id) is not None
        identity_session.rollback()

    assert fetch_independent(identity_session, User, marker_id) is None

    assert fetch_independent(identity_session, User, marker_id) is None
    assert (
        count_independent(
            identity_session,
            select(func.count()).select_from(ResidentBindRequest),
        )
        == 0
    )


def test_composing_bind_request_rolls_back_with_outer_owner(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    with pytest.raises(RuntimeError, match="force outer rollback"):
        with begin_owned_transaction(identity_session):
            marker = User(status="ACTIVE", display_name="tx-owned-marker")
            identity_session.add(marker)
            identity_session.flush()
            created = ResidentBindingService(
                identity_session
            ).create_bind_request_in_owned_transaction(
                actor=principal,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                request_id="d4-tx-compose",
            )
            assert created.status == "PENDING"
            raise RuntimeError("force outer rollback")

    assert (
        count_independent(
            identity_session,
            select(func.count()).select_from(ResidentBindRequest),
        )
        == 0
    )
    leftover = count_independent(
        identity_session,
        select(func.count()).select_from(User).where(User.display_name == "tx-owned-marker"),
    )
    assert leftover == 0


def test_composing_preserves_pending_unflushed_caller_rows(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    with pytest.raises(RuntimeError, match="force outer rollback"):
        with begin_owned_transaction(identity_session):
            marker = User(status="ACTIVE", display_name="tx-pending-marker")
            identity_session.add(marker)
            ResidentBindingService(identity_session).create_bind_request_in_owned_transaction(
                actor=principal,
                house_id=graph.house_1_id,
                relation_type="FAMILY",
                request_id="d4-tx-pending",
            )
            assert marker in identity_session.new or marker.id is not None
            raise RuntimeError("force outer rollback")

    leftover = count_independent(
        identity_session,
        select(func.count()).select_from(User).where(User.display_name == "tx-pending-marker"),
    )
    assert leftover == 0
    assert (
        count_independent(
            identity_session,
            select(func.count()).select_from(ResidentBindRequest),
        )
        == 0
    )


def test_top_level_bind_request_persists_on_independent_session(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=principal,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="d4-tx-toplevel",
    )
    persisted = fetch_independent(identity_session, ResidentBindRequest, created.id)
    assert persisted is not None
    assert persisted.status == "PENDING"


def test_approve_audit_failure_rolls_back_without_manual_rollback(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    reviewer = _principal(identity_session, graph.reviewer_p1_id)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=principal,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="d4-tx-audit-create",
    )

    class FailingAudit:
        def append(self, **values: object) -> None:
            del values
            raise RuntimeError("forced nested audit failure")

    with pytest.raises(RuntimeError, match="forced nested audit failure"):
        ResidentBindingService(
            identity_session,
            audit_service=FailingAudit(),  # type: ignore[arg-type]
        ).approve_bind_request(
            actor=reviewer,
            request_id=created.id,
            http_request_id="d4-tx-audit-approve",
        )
    request = fetch_independent(identity_session, ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"
    assert (
        count_independent(
            identity_session,
            select(func.count()).select_from(ResidentHouseRelation),
        )
        == 0
    )


def test_flushed_insert_without_begin_is_refused_and_caller_can_commit(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    marker = User(status="ACTIVE", display_name="tx-flush-insert")
    identity_session.add(marker)
    identity_session.flush()
    marker_id = marker.id
    assert identity_session.in_transaction()
    with pytest.raises(TransactionContractError, match="unexpected open transaction"):
        ResidentBindingService(identity_session).create_bind_request(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-flush-insert",
        )
    assert identity_session.get(User, marker_id) is not None
    identity_session.commit()
    persisted = fetch_independent(identity_session, User, marker_id)
    assert persisted is not None
    assert persisted.display_name == "tx-flush-insert"


def test_flushed_update_is_refused_and_caller_can_commit(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    user = identity_session.get(User, graph.resident_a_id)
    assert user is not None
    user.display_name = "tx-flush-update"
    identity_session.flush()
    with pytest.raises(TransactionContractError, match="unexpected open transaction"):
        ResidentBindingService(identity_session).create_bind_request(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-flush-update",
        )
    identity_session.commit()
    persisted = fetch_independent(identity_session, User, graph.resident_a_id)
    assert persisted is not None
    assert persisted.display_name == "tx-flush-update"


def test_flushed_delete_is_refused_and_caller_can_commit(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    extra = User(status="ACTIVE", display_name="tx-flush-delete")
    identity_session.add(extra)
    identity_session.commit()
    extra_id = extra.id
    identity_session.delete(extra)
    identity_session.flush()
    with pytest.raises(TransactionContractError, match="unexpected open transaction"):
        ResidentBindingService(identity_session).create_bind_request(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-flush-delete",
        )
    identity_session.commit()
    assert fetch_independent(identity_session, User, extra_id) is None


def test_core_execute_insert_is_refused_and_caller_can_commit(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    result = identity_session.execute(
        insert(User).values(status="ACTIVE", display_name="tx-core-insert")
    )
    marker_id = int(result.lastrowid)
    with pytest.raises(TransactionContractError, match="unexpected open transaction"):
        ResidentBindingService(identity_session).create_bind_request(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-core-insert",
        )
    identity_session.commit()
    persisted = fetch_independent(identity_session, User, marker_id)
    assert persisted is not None
    assert persisted.display_name == "tx-core-insert"


def test_load_principal_does_not_discard_flushed_update_in_explicit_begin(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with identity_session.begin():
        user = identity_session.get(User, graph.resident_a_id)
        assert user is not None
        user.display_name = "tx-auth-explicit"
        identity_session.flush()
        AuthService(
            identity_session,
            IdentityRepository(identity_session),
        ).load_principal(graph.resident_a_id)
        assert identity_session.get(User, graph.resident_a_id).display_name == "tx-auth-explicit"
    persisted = fetch_independent(identity_session, User, graph.resident_a_id)
    assert persisted is not None
    assert persisted.display_name == "tx-auth-explicit"


def test_load_principal_does_not_discard_autobegin_flush(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    user = identity_session.get(User, graph.resident_a_id)
    assert user is not None
    user.display_name = "tx-auth-autobegin"
    identity_session.flush()
    AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    assert identity_session.in_transaction()
    identity_session.commit()
    persisted = fetch_independent(identity_session, User, graph.resident_a_id)
    assert persisted is not None
    assert persisted.display_name == "tx-auth-autobegin"


def test_load_principal_autoflush_keeps_pending_update(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    identity_session.autoflush = True
    try:
        with identity_session.begin():
            user = identity_session.get(User, graph.resident_a_id)
            assert user is not None
            user.display_name = "tx-auth-autoflush"
            AuthService(
                identity_session,
                IdentityRepository(identity_session),
            ).load_principal(graph.resident_a_id)
    finally:
        identity_session.autoflush = False
    persisted = fetch_independent(identity_session, User, graph.resident_a_id)
    assert persisted is not None
    assert persisted.display_name == "tx-auth-autoflush"


def test_query_autobegin_is_not_silently_rolled_back_by_public_use_case(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = _principal(identity_session, graph.resident_a_id)
    identity_session.execute(select(User).limit(1)).first()
    assert identity_session.in_transaction()
    with pytest.raises(TransactionContractError, match="unexpected open transaction"):
        ResidentBindingService(identity_session).create_bind_request(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-query-autobegin",
        )
    assert identity_session.in_transaction()
    identity_session.rollback()
    assert (
        count_independent(
            identity_session,
            select(func.count()).select_from(ResidentBindRequest),
        )
        == 0
    )


def test_reload_principal_inside_owned_transaction_commits_together(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    created_id = 0
    with begin_owned_transaction(identity_session):
        principal = AuthService(
            identity_session,
            IdentityRepository(identity_session),
        ).load_principal(graph.resident_a_id)
        created = ResidentBindingService(
            identity_session
        ).create_bind_request_in_owned_transaction(
            actor=principal,
            house_id=graph.house_1_id,
            relation_type="OWNER",
            request_id="d4-tx-reload-inside",
        )
        created_id = created.id
    persisted = fetch_independent(identity_session, ResidentBindRequest, created_id)
    assert persisted is not None
    assert persisted.status == "PENDING"


def test_analysis_provider_refuses_open_business_transaction(
    identity_session: Session,
) -> None:
    from app.service.analysis import AnalysisService, AnalysisSnapshot

    service = AnalysisService(identity_session)
    identity_session.execute(select(User).limit(1)).first()
    assert identity_session.in_transaction()
    snapshot = AnalysisSnapshot(
        work_order_id=1,
        tenant_id=1,
        property_id=1,
        house_id=1,
        building_id=None,
        analysis_id=1,
        analysis_version=1,
        work_order_version=1,
        input_hash="x",
        text="",
        existing_asr_text=None,
        images=(),
        audio=(),
    )
    with pytest.raises(TransactionContractError, match="must not hold a business transaction"):
        service._call_providers(snapshot=snapshot, request_id="d4-tx-provider", attempt=1)
    assert identity_session.in_transaction()
    identity_session.rollback()
