"""Development Token audit atomicity and sensitive-data checks on MySQL 8."""

import json
from typing import cast

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_auth_service
from app.core.config import Settings
from app.core.database import get_session_factory
from app.main import create_app
from app.model.identity import AuditLog
from app.repository.identity import IdentityRepository
from app.service.audit import AuditService
from app.service.auth import AuthService
from tests.integration.iam_test_data import create_iam_test_graph

TEST_SECRET = "c3-token-audit-secret-that-is-not-production"


class FailingAuditService:
    def append(self, **values: object) -> None:
        del values
        raise RuntimeError("forced token audit failure")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        dev_auth_enabled=True,
        app_secret_key=TEST_SECRET,
    )


def test_successful_dev_token_is_committed_with_safe_audit(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.manager_user_id},
            headers={
                "X-Request-Id": "c3-token-audit",
                "Authorization": "Bearer header-must-not-be-audited",
            },
        )

    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    payload = jwt.decode(token, options={"verify_signature": False})
    audits = identity_session.scalars(
        select(AuditLog).where(AuditLog.request_id == "c3-token-audit")
    ).all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "DEV_AUTH_TOKEN_ISSUED"
    assert audit.actor_user_id == graph.manager_user_id
    assert audit.resource_type == "USER"
    assert audit.resource_id == graph.manager_user_id
    assert audit.tenant_id is None
    assert audit.property_id is None
    assert audit.details_json == {
        "auth_method": "development",
        "token_type": "access",
    }
    serialized = json.dumps(audit.details_json)
    assert token not in serialized
    assert payload["jti"] not in serialized
    assert TEST_SECRET not in serialized
    assert "header-must-not-be-audited" not in serialized


def test_invalid_or_disabled_user_does_not_write_success_audit(
    identity_session: Session,
) -> None:
    graph = create_iam_test_graph(identity_session)
    with TestClient(create_app(_settings())) as client:
        missing = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": 9_223_372_036_854_775_000},
            headers={"X-Request-Id": "c3-missing-user"},
        )
        disabled = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.disabled_user_id},
            headers={"X-Request-Id": "c3-disabled-user"},
        )

    assert missing.status_code == 401
    assert disabled.status_code == 401
    assert identity_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.request_id.in_(
                ("c3-missing-user", "c3-disabled-user")
            )
        )
    ) == 0


def test_audit_failure_returns_no_token_and_rolls_back(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_iam_test_graph(identity_session)
    application = create_app(_settings())

    def failing_auth_service():
        session = get_session_factory(
            integration_settings.test_database_url
        )()
        try:
            yield AuthService(
                session,
                IdentityRepository(session),
                cast(AuditService, FailingAuditService()),
            )
        finally:
            session.close()

    application.dependency_overrides[get_auth_service] = failing_auth_service
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": graph.manager_user_id},
            headers={"X-Request-Id": "c3-audit-failure"},
        )

    assert response.status_code == 500
    body = response.json()
    assert "data" not in body
    assert "access_token" not in json.dumps(body)
    assert identity_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.request_id == "c3-audit-failure")
    ) == 0
