"""Silent-rollback regression: nested services must join the caller transaction."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.model.identity import User
from app.model.resident import ResidentBindRequest
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.resident import ResidentBindingService
from tests.integration.conftest import commit_open_transaction
from tests.integration.resident_test_data import create_resident_binding_graph


def test_nested_service_preserves_flushed_uncommitted_caller_rows(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with pytest.raises(RuntimeError, match="force outer rollback"):
        with identity_session.begin():
            marker = User(status="ACTIVE", display_name="tx-flushed-marker")
            identity_session.add(marker)
            identity_session.flush()
            marker_id = marker.id
            principal = AuthService(
                identity_session,
                IdentityRepository(identity_session),
            ).load_principal(graph.resident_a_id)
            created = ResidentBindingService(identity_session).create_bind_request(
                actor=principal,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                request_id="d4-tx-flushed",
            )
            assert created.status == "PENDING"
            assert identity_session.get(User, marker_id) is not None
            raise RuntimeError("force outer rollback")

    assert identity_session.get(User, marker_id) is None
    assert identity_session.scalar(select(func.count()).select_from(ResidentBindRequest)) == 0


def test_nested_service_preserves_pending_unflushed_caller_rows(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with pytest.raises(RuntimeError, match="force outer rollback"):
        with identity_session.begin():
            marker = User(status="ACTIVE", display_name="tx-pending-marker")
            identity_session.add(marker)
            principal = AuthService(
                identity_session,
                IdentityRepository(identity_session),
            ).load_principal(graph.resident_a_id)
            ResidentBindingService(identity_session).create_bind_request(
                actor=principal,
                house_id=graph.house_1_id,
                relation_type="FAMILY",
                request_id="d4-tx-pending",
            )
            assert marker in identity_session.new or marker.id is not None
            raise RuntimeError("force outer rollback")

    assert identity_session.scalar(select(func.count()).select_from(ResidentBindRequest)) == 0
    leftover = identity_session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.display_name == "tx-pending-marker")
    )
    assert leftover == 0


def test_top_level_bind_request_still_commits(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=principal,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="d4-tx-toplevel",
    )
    identity_session.expire_all()
    persisted = identity_session.get(ResidentBindRequest, created.id)
    assert persisted is not None
    assert persisted.status == "PENDING"


def test_approve_audit_failure_still_rolls_back_together(
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
    commit_open_transaction(identity_session)

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
    identity_session.rollback()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"
    from app.model.resident import ResidentHouseRelation

    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 0
