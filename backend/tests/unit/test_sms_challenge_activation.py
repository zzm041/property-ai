"""A pending OTP must not become usable after an uncertain send or late response."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import AppError
from app.provider.sms import SmsSendResult
from app.service.credential_auth import CredentialAuthService


class FakeRepo:
    def __init__(self):
        self.records = []
        self.latest_override = None

    def get_open_challenge(self, phone, purpose):
        return next((r for r in reversed(self.records) if r.consumed_at is None), None)

    def add_challenge(self, record):
        record.id = len(self.records) + 1
        self.records.append(record)
        return record

    def get_challenge_by_id_for_update(self, challenge_id):
        return next((r for r in self.records if r.id == challenge_id), None)

    def get_latest_challenge_for_update(self, phone, purpose):
        return self.latest_override or self.records[-1]


class FakeProvider:
    def __init__(self, action):
        self.action = action
        self.requests = []

    def send(self, **kwargs):
        self.requests.append(kwargs)
        self.action()
        return SmsSendResult("development", "DEVELOPMENT")


def _service(monkeypatch, repo, provider):
    monkeypatch.setattr("app.service.credential_auth.run_owned_transaction", lambda session, fn: fn())
    settings = Settings(
        _env_file=None, app_env="test", dev_auth_enabled=True,
        app_secret_key="test-challenge-activation-secret",
    )
    service = CredentialAuthService(
        MagicMock(), settings=settings, repository=repo, sms_provider=provider,
        rate_limiter=SimpleNamespace(hit=lambda *args, **kwargs: True),
        otp_factory=lambda: "123456", clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
    )
    service._consume_visual_challenge = lambda **kwargs: "ok"
    return service


def _send(service):
    return service.send_sms(
        phone="13800138000", purpose="login", request_id="send-1",
        challenge_id="visual-1", challenge_answer="1",
    )


def test_provider_failure_leaves_exact_new_challenge_pending(monkeypatch):
    repo = FakeRepo()
    provider = FakeProvider(lambda: (_ for _ in ()).throw(TimeoutError()))
    service = _service(monkeypatch, repo, provider)
    with pytest.raises(AppError):
        _send(service)
    assert len(repo.records) == 1
    assert repo.records[0].consumed_at is not None
    assert repo.get_open_challenge("+8613800138000", "login") is None
    assert provider.requests[0]["request_id"] == "send-1"


def test_success_activates_only_its_own_latest_challenge(monkeypatch):
    repo = FakeRepo()
    provider = FakeProvider(lambda: None)
    service = _service(monkeypatch, repo, provider)
    _send(service)
    assert repo.records[0].consumed_at is None


def test_late_provider_success_cannot_activate_older_code(monkeypatch):
    repo = FakeRepo()
    def newer_request():
        repo.latest_override = SimpleNamespace(id=99)
    provider = FakeProvider(newer_request)
    service = _service(monkeypatch, repo, provider)
    with pytest.raises(AppError):
        _send(service)
    assert repo.records[0].consumed_at is not None
    assert repo.get_open_challenge("+8613800138000", "login") is None


def test_activation_database_failure_keeps_code_pending(monkeypatch):
    repo = FakeRepo()
    def fail_activation(phone, purpose):
        raise OperationalError("injected", {}, Exception("database unavailable"))
    repo.get_latest_challenge_for_update = fail_activation
    service = _service(monkeypatch, repo, FakeProvider(lambda: None))
    with pytest.raises(AppError) as error:
        _send(service)
    assert error.value.status_code == 503
    assert repo.records[0].consumed_at is not None
    assert repo.get_open_challenge("+8613800138000", "login") is None


def test_newer_code_is_not_invalidated_by_late_older_send(monkeypatch):
    repo = FakeRepo()
    newer = SimpleNamespace(id=99, consumed_at=None)
    def publish_newer():
        repo.latest_override = newer
        repo.records.append(newer)
    service = _service(monkeypatch, repo, FakeProvider(publish_newer))
    with pytest.raises(AppError):
        _send(service)
    assert repo.records[0].consumed_at is not None
    assert newer.consumed_at is None
