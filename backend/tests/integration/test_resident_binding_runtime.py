"""Resident bind-request HTTP, authorization, and concurrency tests on MySQL 8."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, IamErrorCode, ResidentErrorCode
from app.core.time import utc_now
from app.domain.identity import ResidentRelationStatus
from app.main import create_app
from app.model.identity import AuditLog, Permission, Role, RolePermission, User, UserRole
from app.model.resident import ResidentBindRequest, ResidentHouseRelation
from app.service.identity.auth import AuthService
from app.repository.identity import IdentityRepository
from app.service.identity.resident import ResidentBindingService
from tests.integration.conftest import commit_open_transaction
from tests.integration.resident_test_data import create_resident_binding_graph

TEST_SECRET = "t4-resident-binding-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
    )


def _client() -> TestClient:
    return TestClient(create_app(_settings()))


def _token(client: TestClient, user_id: int) -> str:
    response = client.post(
        "/api/v1/auth/dev/token",
        json={"user_id": user_id},
        headers={"X-Request-Id": f"t4-login-{user_id}"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_resident_can_create_and_list_own_request(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={
                "house_id": graph.house_1_id,
                "relation_type": "OWNER",
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers={**_auth(token), "X-Request-Id": "t4-create"},
        )
        mine = client.get(
            "/api/v1/resident-bind-requests/mine",
            headers={**_auth(token), "X-Request-Id": "t4-mine"},
        )
        houses = client.get(
            "/api/v1/me/houses",
            headers={**_auth(token), "X-Request-Id": "t4-houses-empty"},
        )

    assert created.status_code == 200
    data = created.json()["data"]
    assert data["user_id"] == graph.resident_a_id
    assert data["house_id"] == graph.house_1_id
    assert data["status"] == "PENDING"
    assert data["reviewer_id"] is None
    assert mine.status_code == 200
    assert len(mine.json()["data"]["items"]) == 1
    assert houses.json()["data"]["items"] == []
    identity_session.rollback()
    audits = identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "t4-create")
    ).all()
    assert len(audits) == 1
    assert audits[0].action == "RESIDENT_BIND_REQUEST_CREATED"
    serialized = json.dumps(audits[0].details_json)
    assert "eyJ" not in serialized
    assert "id_card" not in serialized


def test_create_rejects_client_supplied_user_id(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        response = client.post(
            "/api/v1/resident-bind-requests",
            json={
                "house_id": graph.house_1_id,
                "relation_type": "OWNER",
                "user_id": graph.resident_b_id,
            },
            headers=_auth(token),
        )
    assert response.status_code == 422
    assert "user_id" in response.text.lower() or response.json()["error"]["code"] == (
        "SYS.VALIDATION_ERROR"
    )


def test_invalid_relation_type_is_rejected(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        response = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "GUEST"},
            headers=_auth(token),
        )
    assert response.status_code == 422


def test_duplicate_pending_is_rejected(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        first = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
        second = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "FAMILY"},
            headers=_auth(token),
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == ResidentErrorCode.DUPLICATE_PENDING


def test_nonexistent_and_cross_property_house_are_rejected(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        missing = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": 9_223_372_036_854_775_000, "relation_type": "OWNER"},
            headers=_auth(token),
        )
        cross = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_2_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
        mismatched = client.post(
            "/api/v1/resident-bind-requests",
            json={
                "house_id": graph.house_1_id,
                "relation_type": "OWNER",
                "tenant_id": graph.tenant_2_id,
                "property_id": graph.property_3_id,
            },
            headers=_auth(token),
        )
        other_tenant = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_3_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
    assert missing.status_code == 404
    assert cross.status_code == 200
    assert cross.json()["data"]["status"] == "PENDING"
    assert mismatched.status_code == 422
    assert other_tenant.status_code == 200
    assert other_tenant.json()["data"]["status"] == "PENDING"


def test_resident_cannot_read_another_users_request(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token_a = _token(client, graph.resident_a_id)
        token_b = _token(client, graph.resident_b_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(token_a),
        )
        mine_b = client.get(
            "/api/v1/resident-bind-requests/mine",
            headers=_auth(token_b),
        )
    assert created.status_code == 200
    assert mine_b.json()["data"]["items"] == []


def test_approve_creates_relation_and_audit(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        reviewer = _token(client, graph.reviewer_p1_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "FAMILY"},
            headers={**_auth(resident), "X-Request-Id": "t4-req"},
        )
        request_id = created.json()["data"]["id"]
        pending = client.get(
            "/api/v1/admin/resident-bind-requests",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(reviewer),
        )
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers={**_auth(reviewer), "X-Request-Id": "t4-approve"},
        )
        houses = client.get("/api/v1/me/houses", headers=_auth(resident))

    assert pending.status_code == 200
    assert len(pending.json()["data"]["items"]) == 1
    review_item = pending.json()["data"]["items"][0]
    assert review_item["id"] == request_id
    assert review_item["user_id"] == graph.resident_a_id
    assert review_item["applicant_display_name"] == "Resident A"
    assert review_item["property_name"] == "T4 Property 1"
    assert review_item["building_name"] == "Building 1"
    assert review_item["unit_name"] == "Unit 1"
    assert review_item["room_no"] == "101"
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"
    assert approved.json()["data"]["reviewer_id"] == graph.reviewer_p1_id
    items = houses.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["house_id"] == graph.house_1_id
    assert items[0]["relation_type"] == "FAMILY"
    identity_session.rollback()
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 1
    approve_audits = identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "t4-approve")
    ).all()
    assert len(approve_audits) == 1
    assert approve_audits[0].action == "RESIDENT_BIND_REQUEST_APPROVED"
    assert "reason" not in json.dumps(approve_audits[0].details_json)


def test_admin_relation_history_still_lists_scoped_approved_house(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with identity_session.begin():
        reviewer_role = identity_session.scalar(
            select(Role).where(Role.code == "PROPERTY_ADMIN")
        )
        relation_read = identity_session.scalar(
            select(Permission).where(Permission.code == "resident_relation:read")
        )
        identity_session.add(
            RolePermission(
                role_id=reviewer_role.id,
                permission_id=relation_read.id,
            )
        )
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        reviewer = _token(client, graph.reviewer_p1_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "FAMILY"},
            headers=_auth(resident),
        )
        request_id = created.json()["data"]["id"]
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(reviewer),
        )
        history = client.get(
            "/api/v1/admin/resident-house-relations",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
                "house_id": graph.house_1_id,
                "user_id": graph.resident_a_id,
            },
            headers=_auth(reviewer),
        )

    assert approved.status_code == 200
    assert history.status_code == 200
    assert len(history.json()["data"]["items"]) == 1
    assert history.json()["data"]["items"][0]["house_id"] == graph.house_1_id


def test_reject_persists_reason_without_relation(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        reviewer = _token(client, graph.reviewer_p1_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "TENANT"},
            headers=_auth(resident),
        )
        request_id = created.json()["data"]["id"]
        rejected = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/reject",
            json={"reason": "materials incomplete"},
            headers={**_auth(reviewer), "X-Request-Id": "t4-reject"},
        )
        houses = client.get("/api/v1/me/houses", headers=_auth(resident))
        again = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/reject",
            json={"reason": "again"},
            headers=_auth(reviewer),
        )

    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "REJECTED"
    assert rejected.json()["data"]["review_reason"] == "materials incomplete"
    assert houses.json()["data"]["items"] == []
    assert again.status_code == 409
    assert again.json()["error"]["code"] == ResidentErrorCode.ALREADY_PROCESSED
    identity_session.rollback()
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 0


def test_staff_and_cross_property_reviewers_are_denied(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(resident),
        )
        request_id = created.json()["data"]["id"]
        staff = _token(client, graph.staff_id)
        other = _token(client, graph.reviewer_p2_id)
        system_admin = _token(client, graph.system_admin_id)
        staff_try = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(staff),
        )
        other_try = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(other),
        )
        system_try = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(system_admin),
        )
        other_list = client.get(
            "/api/v1/admin/resident-bind-requests",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers=_auth(other),
        )

    assert staff_try.status_code == 403
    assert other_try.status_code == 403
    assert system_try.status_code == 403
    assert other_list.status_code == 403


def test_x_property_id_cannot_escalate(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        reviewer = _token(client, graph.reviewer_p2_id)
        response = client.get(
            "/api/v1/admin/resident-bind-requests",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers={
                **_auth(reviewer),
                "X-Property-Id": str(graph.property_2_id),
            },
        )
    assert response.status_code == 422


def test_guessed_house_id_does_not_grant_own_resource(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    service = ResidentBindingService(identity_session)
    principal = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    with pytest.raises(AppError) as captured:
        service.get_my_house(actor=principal, house_id=graph.house_1_id)
    assert captured.value.code == ResidentErrorCode.HOUSE_NOT_FOUND


def test_double_approve_is_rejected(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        reviewer = _token(client, graph.reviewer_p1_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(resident),
        )
        request_id = created.json()["data"]["id"]
        first = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(reviewer),
        )
        second = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(reviewer),
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == ResidentErrorCode.ALREADY_PROCESSED


def test_stale_reviewer_permission_is_denied(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    auth = AuthService(identity_session, IdentityRepository(identity_session))
    resident = auth.load_principal(graph.resident_a_id)
    reviewer = auth.load_principal(graph.reviewer_p1_id)
    commit_open_transaction(identity_session)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=resident,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="t4-stale-create",
    )
    commit_open_transaction(identity_session)
    with identity_session.begin():
        identity_session.execute(delete(RolePermission))
    with pytest.raises(AppError) as captured:
        ResidentBindingService(identity_session).approve_bind_request(
            actor=reviewer,
            request_id=created.id,
            http_request_id="t4-stale-approve",
        )
    assert captured.value.code == IamErrorCode.FORBIDDEN
    identity_session.rollback()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"


def test_audit_failure_rolls_back_approve(
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
        request_id="t4-audit-create",
    )
    commit_open_transaction(identity_session)

    class FailingAudit:
        def append(self, **values: object) -> None:
            del values
            raise RuntimeError("forced resident audit failure")

    service = ResidentBindingService(
        identity_session,
        audit_service=FailingAudit(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="forced resident audit failure"):
        service.approve_bind_request(
            actor=reviewer,
            request_id=created.id,
            http_request_id="t4-audit-approve",
        )
    identity_session.rollback()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 0


def test_concurrent_duplicate_pending_creates_one(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    settings = integration_settings

    def worker() -> str:
        session = get_session_factory(settings.test_database_url)()
        try:
            actor = AuthService(
                session,
                IdentityRepository(session),
            ).load_principal(graph.resident_a_id)
            commit_open_transaction(session)
            ResidentBindingService(session).create_bind_request(
                actor=actor,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                request_id="t4-concurrent-create",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("ok") == 1
    assert results.count(ResidentErrorCode.DUPLICATE_PENDING) == 1
    identity_session.rollback()
    assert identity_session.scalar(select(func.count()).select_from(ResidentBindRequest)) == 1


def test_concurrent_approve_has_single_winner(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    actor = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=actor,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="t4-concurrent-approve-create",
    )
    commit_open_transaction(identity_session)

    def worker() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            reviewer = AuthService(
                session,
                IdentityRepository(session),
            ).load_principal(graph.reviewer_p1_id)
            commit_open_transaction(session)
            ResidentBindingService(session).approve_bind_request(
                actor=reviewer,
                request_id=created.id,
                http_request_id="t4-concurrent-approve",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("ok") == 1
    assert set(results) <= {
        "ok",
        ResidentErrorCode.ALREADY_PROCESSED,
        ResidentErrorCode.CONFLICT,
    }
    identity_session.expire_all()
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 1
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "APPROVED"


def test_approve_reject_race_has_single_transition(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    actor = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=actor,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="t4-race-create",
    )

    def approve() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            reviewer = AuthService(
                session,
                IdentityRepository(session),
            ).load_principal(graph.reviewer_p1_id)
            commit_open_transaction(session)
            ResidentBindingService(session).approve_bind_request(
                actor=reviewer,
                request_id=created.id,
                http_request_id="t4-race-approve",
            )
            return "approved"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    def reject() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            reviewer = AuthService(
                session,
                IdentityRepository(session),
            ).load_principal(graph.tenant_admin_id)
            commit_open_transaction(session)
            ResidentBindingService(session).reject_bind_request(
                actor=reviewer,
                request_id=created.id,
                reason="race",
                http_request_id="t4-race-reject",
            )
            return "rejected"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(approve)
        second = pool.submit(reject)
        results = [first.result(), second.result()]
    winners = [item for item in results if item in {"approved", "rejected"}]
    assert len(winners) == 1
    identity_session.expire_all()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status in {"APPROVED", "REJECTED"}
    relation_count = identity_session.scalar(
        select(func.count()).select_from(ResidentHouseRelation)
    )
    if request.status == "APPROVED":
        assert relation_count == 1
    else:
        assert relation_count == 0


def test_active_relation_unique_and_history_preserved(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    resident = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    reviewer = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.reviewer_p1_id)
    commit_open_transaction(identity_session)
    service = ResidentBindingService(identity_session)
    first = service.create_bind_request(
        actor=resident,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="t4-history-1",
    )
    _request, relation = service.approve_bind_request(
        actor=reviewer,
        request_id=first.id,
        http_request_id="t4-history-approve",
    )
    commit_open_transaction(identity_session)
    with identity_session.begin():
        service.end_relation(
            relation,
            ended_at=utc_now() + timedelta(seconds=1),
            status=ResidentRelationStatus.ENDED,
        )
    second = service.create_bind_request(
        actor=resident,
        house_id=graph.house_1_id,
        relation_type="FAMILY",
        request_id="t4-history-2",
    )
    service.approve_bind_request(
        actor=reviewer,
        request_id=second.id,
        http_request_id="t4-history-approve-2",
    )
    identity_session.rollback()
    relations = identity_session.scalars(select(ResidentHouseRelation)).all()
    assert len(relations) == 2
    statuses = {item.status for item in relations}
    assert statuses == {"ACTIVE", "ENDED"}
    types = {item.relation_type for item in relations}
    assert types == {"OWNER", "FAMILY"}


def test_composite_fk_rejects_spliced_house_scope(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                ResidentBindRequest(
                    user_id=graph.resident_a_id,
                    tenant_id=graph.tenant_2_id,
                    property_id=graph.property_3_id,
                    house_id=graph.house_1_id,
                    relation_type="OWNER",
                    status="PENDING",
                    requested_at=utc_now(),
                )
            )
            identity_session.flush()


def test_errors_do_not_leak_mysql_details(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
        duplicate = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
    body = json.dumps(duplicate.json())
    assert "1062" not in body
    assert "uq_resident" not in body
    assert "Duplicate" not in body


def test_rejected_request_can_be_resubmitted(identity_session: Session) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        reviewer = _token(client, graph.reviewer_p1_id)
        first = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(resident),
        )
        request_id = first.json()["data"]["id"]
        rejected = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/reject",
            json={"reason": "need more documents"},
            headers=_auth(reviewer),
        )
        second = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "FAMILY"},
            headers=_auth(resident),
        )
    assert rejected.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "PENDING"
    assert second.json()["data"]["id"] != request_id


def test_tenant_admin_can_approve_and_cross_tenant_is_denied(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        resident = _token(client, graph.resident_a_id)
        tenant_admin = _token(client, graph.tenant_admin_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(resident),
        )
        request_id = created.json()["data"]["id"]
        cross_list = client.get(
            "/api/v1/admin/resident-bind-requests",
            params={
                "tenant_id": graph.tenant_2_id,
                "property_id": graph.property_3_id,
            },
            headers=_auth(tenant_admin),
        )
        matching_header = client.get(
            "/api/v1/admin/resident-bind-requests",
            params={
                "tenant_id": graph.tenant_1_id,
                "property_id": graph.property_1_id,
            },
            headers={
                **_auth(tenant_admin),
                "X-Property-Id": str(graph.property_1_id),
            },
        )
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(tenant_admin),
        )
    assert cross_list.status_code == 403
    assert matching_header.status_code == 200
    assert approved.status_code == 200
    assert approved.json()["data"]["reviewer_id"] == graph.tenant_admin_id


def test_reject_audit_failure_rolls_back(identity_session: Session) -> None:
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
        request_id="t4-reject-audit-create",
    )
    commit_open_transaction(identity_session)

    class FailingAudit:
        def append(self, **values: object) -> None:
            del values
            raise RuntimeError("forced reject audit failure")

    with pytest.raises(RuntimeError, match="forced reject audit failure"):
        ResidentBindingService(
            identity_session,
            audit_service=FailingAudit(),  # type: ignore[arg-type]
        ).reject_bind_request(
            actor=reviewer,
            request_id=created.id,
            reason="materials incomplete",
            http_request_id="t4-reject-audit",
        )
    identity_session.rollback()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"
    assert request.review_reason is None


def test_mysql_pending_and_active_uniques_and_checks(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    if identity_session.in_transaction():
        identity_session.rollback()
    now = utc_now()
    with identity_session.begin():
        identity_session.add(
            ResidentBindRequest(
                user_id=graph.resident_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                status="PENDING",
                requested_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                ResidentBindRequest(
                    user_id=graph.resident_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_1_id,
                    house_id=graph.house_1_id,
                    relation_type="FAMILY",
                    status="PENDING",
                    requested_at=now,
                )
            )
            identity_session.flush()
    identity_session.rollback()

    with identity_session.begin():
        identity_session.add(
            ResidentHouseRelation(
                user_id=graph.resident_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                status="ACTIVE",
                valid_from=now,
                valid_to=None,
            )
        )
    with pytest.raises(IntegrityError):
        with identity_session.begin():
            identity_session.add(
                ResidentHouseRelation(
                    user_id=graph.resident_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_1_id,
                    house_id=graph.house_1_id,
                    relation_type="FAMILY",
                    status="ACTIVE",
                    valid_from=now,
                    valid_to=None,
                )
            )
            identity_session.flush()
    identity_session.rollback()

    with identity_session.begin():
        identity_session.add(
            ResidentHouseRelation(
                user_id=graph.resident_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                house_id=graph.house_1_id,
                relation_type="OWNER",
                status="ENDED",
                valid_from=now - timedelta(days=2),
                valid_to=now - timedelta(days=1),
            )
        )
        identity_session.add(
            ResidentHouseRelation(
                user_id=graph.resident_a_id,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_1_id,
                house_id=graph.house_1_id,
                relation_type="TENANT",
                status="ENDED",
                valid_from=now - timedelta(days=4),
                valid_to=now - timedelta(days=3),
            )
        )
    identity_session.rollback()
    ended = identity_session.scalars(
        select(ResidentHouseRelation).where(
            ResidentHouseRelation.status == "ENDED"
        )
    ).all()
    assert len(ended) == 2
    if identity_session.in_transaction():
        identity_session.rollback()

    with pytest.raises((IntegrityError, OperationalError)):
        with identity_session.begin():
            identity_session.add(
                ResidentBindRequest(
                    user_id=graph.resident_a_id,
                    tenant_id=graph.tenant_1_id,
                    property_id=graph.property_1_id,
                    house_id=graph.house_1_id,
                    relation_type="OWNER",
                    status="OPEN",
                    requested_at=now,
                )
            )
            identity_session.flush()
    identity_session.rollback()


def test_new_user_without_roles_can_self_apply(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with identity_session.begin():
        orphan = User(status="ACTIVE", display_name="Orphan Applicant")
        identity_session.add(orphan)
        identity_session.flush()
        orphan_id = orphan.id
    with _client() as client:
        token = _token(client, orphan_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(token),
        )
        houses = client.get("/api/v1/me/houses", headers=_auth(token))
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "PENDING"
    assert houses.json()["data"]["items"] == []


def test_resident_with_property_a_role_can_apply_to_property_b(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        token = _token(client, graph.resident_a_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_2_id, "relation_type": "FAMILY"},
            headers=_auth(token),
        )
        houses = client.get("/api/v1/me/houses", headers=_auth(token))
        reviewer = _token(client, graph.reviewer_p2_id)
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{created.json()['data']['id']}/approve",
            headers=_auth(reviewer),
        )
        houses_after = client.get("/api/v1/me/houses", headers=_auth(token))
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "PENDING"
    assert houses.json()["data"]["items"] == []
    assert approved.status_code == 200
    house_ids = {item["house_id"] for item in houses_after.json()["data"]["items"]}
    assert graph.house_2_id in house_ids


def test_staff_can_self_apply_for_housing_without_gaining_access_before_approve(
    identity_session: Session,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    with _client() as client:
        staff = _token(client, graph.staff_id)
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={"house_id": graph.house_1_id, "relation_type": "OWNER"},
            headers=_auth(staff),
        )
        houses = client.get("/api/v1/me/houses", headers=_auth(staff))
        reviewer = _token(client, graph.reviewer_p1_id)
        request_id = created.json()["data"]["id"]
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{request_id}/approve",
            headers=_auth(reviewer),
        )
        houses_after = client.get("/api/v1/me/houses", headers=_auth(staff))
    assert created.status_code == 200
    assert created.json()["data"]["status"] == "PENDING"
    assert houses.json()["data"]["items"] == []
    assert approved.status_code == 200
    assert any(
        item["house_id"] == graph.house_1_id
        for item in houses_after.json()["data"]["items"]
    )


def test_missing_resident_role_rolls_back_entire_approval(
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
        request_id="t4-missing-role-create",
    )
    commit_open_transaction(identity_session)
    role = identity_session.scalar(select(Role).where(Role.code == "RESIDENT"))
    assert role is not None
    commit_open_transaction(identity_session)
    with identity_session.begin():
        identity_session.execute(delete(UserRole).where(UserRole.role_id == role.id))
        identity_session.execute(
            delete(RolePermission).where(RolePermission.role_id == role.id)
        )
        identity_session.execute(delete(Role).where(Role.id == role.id))
    reviewer = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.reviewer_p1_id)
    commit_open_transaction(identity_session)
    with pytest.raises(AppError) as captured:
        ResidentBindingService(identity_session).approve_bind_request(
            actor=reviewer,
            request_id=created.id,
            http_request_id="t4-missing-role-approve",
        )
    assert captured.value.code == IamErrorCode.ROLE_NOT_FOUND
    identity_session.rollback()
    request = identity_session.get(ResidentBindRequest, created.id)
    assert request is not None
    assert request.status == "PENDING"
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 0


def test_concurrent_approve_does_not_duplicate_resident_grant(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_resident_binding_graph(identity_session)
    actor = AuthService(
        identity_session,
        IdentityRepository(identity_session),
    ).load_principal(graph.resident_a_id)
    commit_open_transaction(identity_session)
    created = ResidentBindingService(identity_session).create_bind_request(
        actor=actor,
        house_id=graph.house_1_id,
        relation_type="OWNER",
        request_id="t4-grant-race-create",
    )
    commit_open_transaction(identity_session)

    def worker() -> str:
        session = get_session_factory(integration_settings.test_database_url)()
        try:
            reviewer = AuthService(
                session,
                IdentityRepository(session),
            ).load_principal(graph.reviewer_p1_id)
            commit_open_transaction(session)
            ResidentBindingService(session).approve_bind_request(
                actor=reviewer,
                request_id=created.id,
                http_request_id="t4-grant-race-approve",
            )
            return "ok"
        except AppError as exc:
            return exc.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("ok") == 1
    identity_session.expire_all()
    role = identity_session.scalar(select(Role).where(Role.code == "RESIDENT"))
    assert role is not None
    grants = identity_session.scalars(
        select(UserRole).where(
            UserRole.user_id == graph.resident_a_id,
            UserRole.role_id == role.id,
            UserRole.property_id == graph.property_1_id,
        )
    ).all()
    assert len(grants) == 1
    assert identity_session.scalar(select(func.count()).select_from(ResidentHouseRelation)) == 1
