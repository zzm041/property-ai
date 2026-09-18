"""Owned vs composing transaction contract: no autobegin join, no silent discard."""

import pytest
from sqlalchemy import func, select
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
from tests.integration.conftest import commit_open_transaction, count_independent, fetch_independent
from tests.integration.resident_test_data import create_resident_binding_graph


def test_public_create_refuses_unexpected_open_transaction_without_discarding_caller(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
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
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
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
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
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
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
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
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    reviewer = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.reviewer_p1_id)
    commit_open_transaction(identity_session)
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
