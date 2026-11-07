"""Real-time RBAC and Scope security tests with test-only policy routes."""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_principal
from app.core.config import Settings
from app.core.database import get_engine
from app.domain.iam import OrganizationScope, Principal, ScopeType
from app.main import create_app
from app.model.identity import RolePermission, UserRole
from app.schema.common import success_envelope
from app.service.rbac import RbacService
from tests.integration.iam_test_data import IamTestGraph, create_iam_test_graph

TEST_SECRET = "gate-b-rbac-secret-that-is-not-production"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
    )


def _policy_app(settings: Settings) -> FastAPI:
    application = create_app(settings)

    @application.get("/api/v1/_test/properties/{tenant_id}/{property_id}")
    def property_policy_harness(
        tenant_id: int,
        property_id: int,
        request: Request,
        principal: Annotated[Principal, Depends(get_current_principal)],
        permission: str = "property:read",
        x_property_id: Annotated[
            int | None,
            Header(alias="X-Property-Id"),
        ] = None,
    ) -> dict[str, object]:
        del x_property_id
        RbacService.require_permission(
            principal,
            permission,
            OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=tenant_id,
                property_id=property_id,
            ),
        )
        return success_envelope(request, {"authorized": True})

    return application


def _issue_token(client: TestClient, user_id: int) -> str:
    response = client.post(
        "/api/v1/auth/dev/token",
        json={"user_id": user_id},
    )
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _property_request(
    client: TestClient,
    token: str,
    *,
    tenant_id: int,
    property_id: int,
    permission: str = "property:read",
    headers: dict[str, str] | None = None,
):
    return client.get(
        f"/api/v1/_test/properties/{tenant_id}/{property_id}",
        params={"permission": permission},
        headers={**_authorization(token), **(headers or {})},
    )


def test_property_scope_denies_cross_property_and_cross_tenant(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.manager_user_id)
        own = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        cross_property = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_2_id,
        )
        cross_tenant = _property_request(
            client,
            token,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
        )

    assert own.status_code == 200
    assert cross_property.status_code == 403
    assert cross_tenant.status_code == 403


def test_tenant_scope_allows_own_properties_only(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.tenant_admin_user_id)
        first = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        second = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_2_id,
        )
        cross_tenant = _property_request(
            client,
            token,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert cross_tenant.status_code == 403


def test_system_scope_still_requires_permission(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.system_admin_user_id)
        allowed = _property_request(
            client,
            token,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
            permission="property:manage",
        )
        denied = _property_request(
            client,
            token,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
            permission="audit:delete",
        )

    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_resident_cannot_use_manager_permission(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.resident_user_id)
        response = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
            permission="property:manage",
            headers={"X-Request-Id": "resident-denied"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "request_id": "resident-denied",
        "error": {
            "code": "IAM.FORBIDDEN",
            "message": "Permission denied",
            "details": {},
        },
    }


def test_forged_property_header_and_guessed_id_do_not_elevate(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.manager_user_id)
        response = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_2_id,
            headers={"X-Property-Id": str(graph.property_1_id)},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "IAM.FORBIDDEN"


def test_multiple_database_roles_form_permission_union(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        token = _issue_token(client, graph.multi_role_user_id)
        response = client.get("/api/v1/me", headers=_authorization(token))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["roles"] == ["PROPERTY_ADMIN", "STAFF"]
    assert data["permissions"] == [
        "property:manage",
        "property:read",
        "task:read",
    ]


def test_permission_revoke_takes_effect_with_same_token(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.manager_user_id)
        before = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
            permission="property:manage",
        )

        with identity_session.begin():
            role_permission = identity_session.get(
                RolePermission,
                graph.manager_manage_role_permission_id,
            )
            assert role_permission is not None
            identity_session.delete(role_permission)

        after = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
            permission="property:manage",
        )

    assert before.status_code == 200
    assert after.status_code == 403
    assert after.json()["error"]["code"] == "IAM.FORBIDDEN"


def test_role_revoke_takes_effect_with_same_token(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.manager_user_id)
        before = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )

        with identity_session.begin():
            grant = identity_session.get(UserRole, graph.manager_grant_id)
            assert grant is not None
            identity_session.delete(grant)

        after = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )

    assert before.status_code == 200
    assert after.status_code == 403


def test_auth_session_is_closed_before_business_policy_runs(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_iam_test_graph(identity_session)
    engine = get_engine(integration_settings.test_database_url)
    with TestClient(_policy_app(_settings())) as client:
        token = _issue_token(client, graph.manager_user_id)
        response = _property_request(
            client,
            token,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        checked_out = engine.pool.checkedout()

    assert response.status_code == 200
    assert checked_out == 0
