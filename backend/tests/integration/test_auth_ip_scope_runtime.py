"""PA-168: the per-client-IP credential budget has to survive the reverse proxy.

In the delivery stack nginx terminates every request, so the socket peer the api
sees is the nginx container. Unless that hop is declared trusted, every caller
shares one IP bucket: thirty logins an hour throttle the whole deployment, and a
spoofed ``X-Forwarded-For`` is indistinguishable from a real client. These tests
drive the real ``get_client_ip`` dependency through the HTTP surface, so both
halves are pinned: the proxy hop is ignored by default, and the budget is
per-client once it is trusted.
"""

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.core.rate_limit import MemoryRateLimiter
from app.main import create_app

TEST_SECRET = "pa168-integration-secret-that-is-not-production"
# What a reverse proxy on the compose network hands to the api as the peer.
PROXY_PEER = ("172.18.0.2", 51234)
PROXY_NETWORK = "172.18.0.0/16"
CLIENT_A = "203.0.113.7"
CLIENT_B = "198.51.100.9"
LOGIN_PATH = "/api/v1/auth/password/login"


class _PeerAsProxy:
    """ASGI stand-in for the socket peer a reverse proxy produces.

    ``TestClient`` reports the peer as ``("testclient", 50000)``, which is not an
    address at all, so the proxy hop could never be exercised. Rewriting the
    HTTP scope gives the app the peer nginx really produces.
    """

    def __init__(self, app, peer: tuple[str, int] = PROXY_PEER) -> None:
        self._app = app
        self._peer = peer

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope = dict(scope)
            scope["client"] = self._peer
        await self._app(scope, receive, send)


def _settings(*, trusted_proxy_cidrs: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key=TEST_SECRET,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )


@pytest.fixture
def shared_limiter(monkeypatch: pytest.MonkeyPatch) -> MemoryRateLimiter:
    """One in-process limiter for the whole request sequence.

    ``app_env=test`` builds a fresh ``MemoryRateLimiter`` per request, which
    cannot accumulate, so the budget would never trip. Sharing one instance is
    what ``RedisRateLimiter`` does in a real deployment.
    """

    limiter = MemoryRateLimiter()
    monkeypatch.setattr("app.api.v1.credential_auth.MemoryRateLimiter", lambda: limiter)
    return limiter


def _login(client: TestClient, *, phone: str, client_ip: str):
    return client.post(
        LOGIN_PATH,
        json={"phone": phone, "password": "x"},
        headers={"X-Forwarded-For": client_ip},
    )


def test_an_untrusted_proxy_collapses_every_client_into_one_bucket(
    shared_limiter: MemoryRateLimiter,
) -> None:
    settings = _settings(trusted_proxy_cidrs="")
    limit = settings.sms_ip_limit_per_hour
    with TestClient(_PeerAsProxy(create_app(settings))) as client:
        # Every request claims a different client address, and the phone is
        # unique every time, so only the proxy bucket can accumulate.
        for index in range(limit):
            response = _login(
                client,
                phone=f"13900000{index:03d}",
                client_ip=f"203.0.113.{index + 10}",
            )
            assert response.status_code == 401, response.text
        collapsed = _login(client, phone="13900009990", client_ip=CLIENT_B)

    assert collapsed.status_code == 429, collapsed.text
    # The blocked caller never sent this address before: the bucket that
    # tripped belongs to the proxy, not to any client.
    assert len(shared_limiter._hits[f"sms:login:ip:{PROXY_PEER[0]}"]) == limit


def test_a_trusted_proxy_gives_each_client_its_own_bucket(
    shared_limiter: MemoryRateLimiter,
) -> None:
    settings = _settings(trusted_proxy_cidrs=PROXY_NETWORK)
    limit = settings.sms_ip_limit_per_hour
    with TestClient(_PeerAsProxy(create_app(settings))) as client:
        for index in range(limit):
            response = _login(
                client,
                phone=f"13800000{index:03d}",
                client_ip=CLIENT_A,
            )
            assert response.status_code == 401, response.text
        exhausted = _login(client, phone="13800009990", client_ip=CLIENT_A)
        unaffected = _login(client, phone="13800009991", client_ip=CLIENT_B)

    assert exhausted.status_code == 429, exhausted.text
    # A second client keeps its own budget: this is the regression PA-168 fixes.
    assert unaffected.status_code == 401, unaffected.text


def test_the_two_dimensions_are_indistinguishable_from_the_outside(
    shared_limiter: MemoryRateLimiter,
) -> None:
    settings = _settings(trusted_proxy_cidrs=PROXY_NETWORK)
    phone_limit = settings.sms_login_limit_per_hour
    ip_limit = settings.sms_ip_limit_per_hour
    with TestClient(_PeerAsProxy(create_app(settings))) as client:
        for _ in range(phone_limit):
            assert (
                _login(client, phone="13700000000", client_ip=CLIENT_A).status_code
                == 401
            )
        phone_blocked = _login(client, phone="13700000000", client_ip=CLIENT_A)
        # A different client fills its own IP budget with fresh phones.
        for index in range(ip_limit):
            assert (
                _login(
                    client, phone=f"13600000{index:03d}", client_ip=CLIENT_B
                ).status_code
                == 401
            )
        ip_blocked = _login(client, phone="13600009999", client_ip=CLIENT_B)

    assert phone_blocked.status_code == 429, phone_blocked.text
    assert ip_blocked.status_code == 429, ip_blocked.text
    phone_error = phone_blocked.json()["error"]
    ip_error = ip_blocked.json()["error"]
    # Same code, same message, empty details: a caller cannot learn which budget
    # it hit, and neither message names a phone or an address.
    assert phone_error == ip_error
    assert phone_error["code"] == "AUTH.SMS_RATE_LIMITED"
    assert phone_error["details"] == {}
    lowered = phone_error["message"].lower()
    for forbidden in ("phone", "ip", "address", "13700000000", CLIENT_A):
        assert forbidden not in lowered
