"""Development Auth and /me behavior on real MySQL 8."""

from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import issue_development_access_token
from app.core.time import utc_now
from app.main import create_app
from app.model.identity import User
from tests.integration.iam_test_data import create_iam_test_graph

TEST_SECRET = "gate-b-integration-secret-that-is-not-production"


def _settings(*, dev_auth_enabled: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=dev_auth_enabled,
        app_secret_key=TEST_SECRET,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_active_user_can_login_and_me_is_database_derived(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        login = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.manager_user_id},
            headers={"X-Request-Id": "gate-b-login"},
        )
        assert login.status_code == 200
        assert login.headers["X-Request-Id"] == "gate-b-login"
        token_data = login.json()["data"]
        token = token_data["access_token"]
        assert token_data["token_type"] == "bearer"
        assert token_data["expires_in"] == 900

        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["sub"] == str(graph.manager_user_id)
        assert not {
            "role",
            "roles",
            "permission",
            "permissions",
            "scope",
            "tenant_id",
            "property_id",
        } & set(payload)

        response = client.get(
            "/api/v1/me",
            headers={**_bearer(token), "X-Request-Id": "gate-b-me"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "gate-b-me"
    data = response.json()["data"]
    assert data["user_id"] == graph.manager_user_id
    assert data["status"] == "ACTIVE"
    assert data["display_name"] == "Manager"
    assert data["roles"] == ["PROPERTY_ADMIN"]
    assert data["permissions"] == ["property:manage", "property:read"]
    assert data["scopes"] == [
        {
            "role_code": "PROPERTY_ADMIN",
            "scope_type": "PROPERTY",
            "tenant_id": graph.tenant_1_id,
            "property_id": graph.property_1_id,
            "permissions": ["property:manage", "property:read"],
        }
    ]


def test_nonexistent_user_login_is_rejected(identity_session: Session) -> None:
    create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": 9223372036854775807},
            headers={"X-Request-Id": "missing-user"},
        )

    assert response.status_code == 401
    assert response.json() == {
        "request_id": "missing-user",
        "error": {
            "code": "AUTH.INVALID_USER",
            "message": "Development user is invalid",
            "details": {},
        },
    }


def test_disabled_user_login_is_rejected(identity_session: Session) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.disabled_user_id},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH.USER_DISABLED"


@pytest.mark.parametrize(
    "forbidden_field",
    ["role", "permission", "tenant_id", "property_id", "scope"],
)
def test_development_login_forbids_authorization_fields(
    identity_session: Session,
    forbidden_field: str,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={
                "user_id": graph.resident_user_id,
                forbidden_field: "SYSTEM_ADMIN",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SYS.VALIDATION_ERROR"


def test_dev_auth_disabled_route_is_not_mounted() -> None:
    with TestClient(create_app(_settings(dev_auth_enabled=False))) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": 1},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SYS.NOT_FOUND"


def test_dev_auth_disabled_does_not_accept_existing_development_token() -> None:
    token = issue_development_access_token(1, _settings())
    with TestClient(create_app(_settings(dev_auth_enabled=False))) as client:
        response = client.get("/api/v1/me", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH.INVALID_TOKEN"


def test_me_requires_bearer_token() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get(
            "/api/v1/me",
            headers={"X-Request-Id": "missing-token"},
        )

    assert response.status_code == 401
    assert response.json()["request_id"] == "missing-token"
    assert response.json()["error"]["code"] == "AUTH.MISSING_TOKEN"


def test_me_rejects_malformed_and_expired_tokens(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    expired = issue_development_access_token(
        graph.manager_user_id,
        _settings(),
        now=utc_now() - timedelta(seconds=901),
    )
    with TestClient(create_app(_settings())) as client:
        malformed_response = client.get(
            "/api/v1/me",
            headers=_bearer("not-a-jwt"),
        )
        expired_response = client.get(
            "/api/v1/me",
            headers=_bearer(expired),
        )

    assert malformed_response.status_code == 401
    assert malformed_response.json()["error"]["code"] == "AUTH.INVALID_TOKEN"
    assert expired_response.status_code == 401
    assert expired_response.json()["error"]["code"] == "AUTH.TOKEN_EXPIRED"


def test_user_disabled_after_issue_is_rejected_on_next_request(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        login = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.manager_user_id},
        )
        token = login.json()["data"]["access_token"]

        with identity_session.begin():
            user = identity_session.get(User, graph.manager_user_id)
            assert user is not None
            user.status = "DISABLED"

        response = client.get("/api/v1/me", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH.USER_DISABLED"


def test_auth_logs_do_not_include_token_or_secret(
    identity_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        login = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.manager_user_id},
        )
        token = login.json()["data"]["access_token"]
        client.get("/api/v1/me", headers=_bearer(token))

    output = capsys.readouterr().out
    assert token not in output
    assert TEST_SECRET not in output
