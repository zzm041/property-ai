"""Three-send offline auth flow on the named, preserved 3317 synthetic database."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.database import begin_owned_transaction, get_session_factory
from app.main import create_app
from app.model.identity import Permission, Role, RolePermission, SmsChallenge, User, UserRole
from app.provider.sms import SmsSendResult
from app.service.identity.property import PropertyService

from backend.tests.integration import test_isolated_auth_runtime as auth_scenario


def _settings(*, reviewer_dev_auth=False):
    database_url = os.getenv("AUTH_SMS_3317_DATABASE_URL", "")
    redis_url = os.getenv("AUTH_SMS_3317_REDIS_URL", "")
    if not database_url or not redis_url:
        pytest.skip("named 3317 database and isolated Redis URLs are not configured")
    target = make_url(database_url)
    assert (target.host, target.port, target.database) == (
        "127.0.0.1", 3317, "property_ai_auth001_test"
    )
    return Settings(
        _env_file=None,
        app_env="test",
        allow_destructive_test_database=True,
        test_database_url=database_url,
        database_url="mysql+pymysql://property:unused@127.0.0.1:3306/property_ai",
        redis_url=redis_url,
        sms_provider="development",
        dev_auth_enabled=reviewer_dev_auth,
        wechat_login_enabled=True,
        wechat_provider="development",
        app_secret_key="auth-sms-3317-test-secret-not-production",
        jwt_issuer="test-issuer",
        jwt_audience="test-audience",
    )


def test_new_user_initial_password_and_separate_forgotten_password_on_3317():
    with TestClient(create_app(_settings())) as client:
        auth_scenario.run_full_sms_auth_flow(client)


def _synthetic_house_and_reviewer(settings):
    with get_session_factory(settings.test_database_url)() as session:
        service = PropertyService(session)
        suffix = uuid4().hex[:10]
        tenant = service.create_tenant(name=f"sms-offline-tenant-{suffix}")
        tenant_id = tenant.id
        property_record = service.create_property(
            tenant_id=tenant_id, name=f"sms-offline-property-{suffix}", address="synthetic"
        )
        property_id = property_record.id
        building = service.create_building(
            tenant_id=tenant_id, property_id=property_id, code="B1", name="Synthetic"
        )
        unit = service.create_unit(
            tenant_id=tenant_id, property_id=property_id,
            building_id=building.id, code="U1", name="Synthetic"
        )
        house = service.create_house(
            tenant_id=tenant_id, property_id=property_id, unit_id=unit.id, room_no="101"
        )
        house_id = house.id
        with session.begin():
            role = session.scalar(select(Role).where(Role.code == "PROPERTY_ADMIN"))
            if role is None:
                role = Role(code="PROPERTY_ADMIN", name="Synthetic property reviewer")
                session.add(role)
                session.flush()
            for code in ("resident_binding:review", "resident_binding:read"):
                permission = session.scalar(select(Permission).where(Permission.code == code))
                assert permission is not None
                link = session.scalar(select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission_id == permission.id,
                ))
                if link is None:
                    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
            reviewer = User(status="ACTIVE", display_name="SMS offline reviewer")
            session.add(reviewer)
            session.flush()
            reviewer_id = reviewer.id
            session.add(UserRole(
                user_id=reviewer_id, role_id=role.id, scope_type="PROPERTY",
                tenant_id=tenant_id, property_id=property_id,
            ))
        return tenant_id, property_id, house_id, reviewer_id


def test_three_sms_flow_continues_to_house_review_and_permissions_on_3317():
    settings = _settings(reviewer_dev_auth=True)
    tenant_id, property_id, house_id, reviewer_id = _synthetic_house_and_reviewer(settings)
    with TestClient(create_app(settings)) as client:
        _, recovered = auth_scenario.run_full_sms_auth_flow(client)
        resident_id = recovered["user_id"]
        resident_headers = {"Authorization": f"Bearer {recovered['access_token']}"}
        before = client.get("/api/v1/me/houses", headers=resident_headers)
        assert before.status_code == 200
        assert before.json()["data"]["items"] == []
        created = client.post(
            "/api/v1/resident-bind-requests",
            json={
                "house_id": house_id, "relation_type": "OWNER",
                "tenant_id": tenant_id, "property_id": property_id,
            },
            headers={**resident_headers, "X-Request-Id": "sms-house-application"},
        )
        assert created.status_code == 200, created.text
        request = created.json()["data"]
        assert request["user_id"] == resident_id
        assert request["status"] == "PENDING"
        self_review = client.post(
            f"/api/v1/admin/resident-bind-requests/{request['id']}/approve",
            headers={**resident_headers, "X-Request-Id": "sms-self-review-denied"},
        )
        assert self_review.status_code == 403
        reviewer_token = client.post(
            "/api/v1/auth/dev/token", json={"user_id": reviewer_id},
            headers={"X-Request-Id": "sms-synthetic-reviewer"},
        )
        assert reviewer_token.status_code == 200, reviewer_token.text
        reviewer_headers = {
            "Authorization": f"Bearer {reviewer_token.json()['data']['access_token']}"
        }
        approved = client.post(
            f"/api/v1/admin/resident-bind-requests/{request['id']}/approve",
            headers={**reviewer_headers, "X-Request-Id": "sms-house-approved"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "APPROVED"
        after = client.get("/api/v1/me/houses", headers=resident_headers)
        assert after.status_code == 200
        assert [row["house_id"] for row in after.json()["data"]["items"]] == [house_id]
        me = client.get("/api/v1/me", headers=resident_headers)
        assert me.status_code == 200
        assert "RESIDENT" in me.json()["data"]["roles"]


def test_late_first_send_cannot_activate_or_cancel_second_on_3317(monkeypatch):
    first_entered = Event()
    release_first = Event()
    lock = Lock()

    class DelayedProvider:
        def __init__(self):
            self.codes = []

        def send(self, *, phone_e164, purpose, code, request_id):
            with lock:
                self.codes.append(code)
                number = len(self.codes)
            if number == 1:
                first_entered.set()
                if not release_first.wait(10):
                    raise TimeoutError("test sender was not released")
            return SmsSendResult("development", "DEVELOPMENT")

    provider = DelayedProvider()
    monkeypatch.setattr(
        "app.service.identity.credential_auth.build_sms_provider", lambda settings: provider
    )
    with TestClient(create_app(_settings())) as client:
        phone = auth_scenario._phone()
        first_id, first_answer = auth_scenario._issue(client, phone)
        second_id, second_answer = auth_scenario._issue(client, phone)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                auth_scenario._send, client, phone, "login", first_id, first_answer
            )
            try:
                assert first_entered.wait(5)
                second = pool.submit(
                    auth_scenario._send, client, phone, "login", second_id, second_answer
                ).result(timeout=10)
                assert second.status_code == 200, second.text
            finally:
                release_first.set()
            late = first.result(timeout=10)
        assert late.status_code == 503
        assert auth_scenario._error(late) == "AUTH.SMS_PROVIDER_DOWN"
        assert len(provider.codes) == 2
        old = client.post(
            "/api/v1/auth/sms/login",
            json={"phone": phone, "code": provider.codes[0]},
            headers={"X-Request-Id": "late-old-code"},
        )
        assert old.status_code == 401
        new = client.post(
            "/api/v1/auth/sms/login",
            json={"phone": phone, "code": provider.codes[1]},
            headers={"X-Request-Id": "late-new-code"},
        )
        assert new.status_code == 200, new.text


def test_activation_commit_failure_rolls_back_and_code_stays_unusable_on_3317(
    monkeypatch,
):
    from app.service.identity import credential_auth

    original_transaction = credential_auth.run_owned_transaction

    def injected_transaction(session, operation):
        if operation.__name__ == "_activate":
            with begin_owned_transaction(session):
                operation()
                pending = next(obj for obj in session.dirty if isinstance(obj, SmsChallenge))
                session.flush()
                assert session.scalar(select(SmsChallenge).where(
                    SmsChallenge.id == pending.id,
                    SmsChallenge.consumed_at.is_(None),
                )) is not None
                raise OperationalError("injected commit failure", {}, Exception("offline"))
        return original_transaction(session, operation)

    monkeypatch.setattr(credential_auth, "run_owned_transaction", injected_transaction)
    with TestClient(create_app(_settings())) as client:
        phone = auth_scenario._phone()
        challenge_id, answer = auth_scenario._issue(client, phone)
        failed = auth_scenario._send(client, phone, "login", challenge_id, answer)
        assert failed.status_code == 503
        assert auth_scenario._error(failed) == "AUTH.SMS_PROVIDER_DOWN"
        code = auth_scenario._inbox(client, phone, "login")
        rejected = client.post(
            "/api/v1/auth/sms/login", json={"phone": phone, "code": code},
            headers={"X-Request-Id": "activation-rollback-code"},
        )
        assert rejected.status_code == 401
        assert auth_scenario._error(rejected) == "AUTH.INVALID_CODE"
