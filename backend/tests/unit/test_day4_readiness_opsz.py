"""Day 4 readiness honesty and opsz access control."""

from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_principal
from app.core.config import Settings
from app.core.readiness import NOT_CHECKED, check_readiness
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.main import create_app


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        wechat_login_enabled=False,
        celery_task_queue="property_ai.test",
        milvus_collection="knowledge_chunks_test",
        storage_bucket="property-ai-test",
        storage_provider="development",
    )


def test_unprobed_vendors_are_not_checked_not_up() -> None:
    result, status = check_readiness(
        _settings(),
        probes={
            "mysql": (True, lambda _s: None),
            "llm": (False, NOT_CHECKED),
            "storage": (False, NOT_CHECKED),
        },
    )
    assert status == 200
    assert result["status"] == "ready"
    assert result["dependencies"]["llm"]["status"] == "not_checked"
    assert result["dependencies"]["storage"]["reason"] == "live_vendor_not_probed"
    assert result["dependencies"]["mysql"]["status"] == "up"
    assert result["dependencies"]["mysql"]["checked_at"]


def test_opsz_forbids_property_roles_and_allows_system_audit_read() -> None:
    settings = _settings()
    app = create_app(settings)

    resident = Principal(
        user_id=2,
        status="ACTIVE",
        display_name="Resident",
        grants=(
            RoleGrant(
                role_code="RESIDENT",
                scope=OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=1),
                permissions=frozenset({"work_order:create", "work_order:read", "audit:read"}),
            ),
        ),
    )
    dispatcher = Principal(
        user_id=3,
        status="ACTIVE",
        display_name="Supervisor",
        grants=(
            RoleGrant(
                role_code="SUPERVISOR",
                scope=OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=1),
                permissions=frozenset({"work_order:dispatch", "work_order:read", "audit:read"}),
            ),
        ),
    )
    operator = Principal(
        user_id=4,
        status="ACTIVE",
        display_name="System operator",
        grants=(
            RoleGrant(
                role_code="SYSTEM_ADMIN",
                scope=OrganizationScope(ScopeType.SYSTEM),
                permissions=frozenset({"audit:read"}),
            ),
        ),
    )

    app.dependency_overrides[get_current_principal] = lambda: resident
    with TestClient(app) as client:
        forbidden = client.get("/api/v1/opsz")
    assert forbidden.status_code == 403

    app.dependency_overrides[get_current_principal] = lambda: dispatcher
    with TestClient(app) as client:
        dispatcher_forbidden = client.get("/api/v1/opsz")
    assert dispatcher_forbidden.status_code == 403

    app.dependency_overrides[get_current_principal] = lambda: operator
    with TestClient(app) as client:
        allowed = client.get("/api/v1/opsz")
    assert allowed.status_code == 200
    data = allowed.json()["data"]
    assert data["celery_queue"] == "property_ai.test"
    assert "secret" not in str(allowed.json()).casefold() or "secret_key" not in str(allowed.json()).casefold()
