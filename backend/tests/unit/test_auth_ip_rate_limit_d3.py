"""D3: the credential limits must hold on two dimensions at once.

Phone-only limits are bypassed by rotating numbers, so the guard also counts per
client IP. The IP is only believed from ``X-Forwarded-For`` behind a trusted
proxy - otherwise the header is attacker-controlled and ignored.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import AppError, AuthErrorCode
from app.core.rate_limit import (
    AuthRateLimitGuard,
    MemoryRateLimiter,
    parse_trusted_proxies,
    resolve_client_ip,
)
from app.service.identity.credential_auth import CredentialAuthService

BACKEND = Path(__file__).resolve().parents[2]


def _settings(**overrides: object) -> Settings:
    base = {
        "app_env": "test",
        "sms_send_limit_per_hour": 2,
        "sms_login_limit_per_hour": 2,
        "sms_ip_limit_per_hour": 3,
        "trusted_proxy_cidrs": "",
    }
    base.update(overrides)
    return Settings(**base)


def _guard(
    *,
    phone_limit: int = 2,
    ip_limit: int = 3,
    scope: str = "sms:send",
) -> AuthRateLimitGuard:
    return AuthRateLimitGuard(
        limiter=MemoryRateLimiter(clock=lambda: datetime(2026, 11, 11, tzinfo=UTC)),
        phone_limits={scope: phone_limit},
        ip_limit=ip_limit,
    )


def _limited(callable_) -> AppError:
    with pytest.raises(AppError) as error:
        callable_()
    return error.value


def test_same_phone_repeatedly_trips_the_limit() -> None:
    guard = _guard(phone_limit=2, ip_limit=100)
    guard.enforce(scope="sms:send", phone="+8613800138000", client_ip="203.0.113.9")
    guard.enforce(scope="sms:send", phone="+8613800138000", client_ip="203.0.113.9")
    error = _limited(
        lambda: guard.enforce(
            scope="sms:send", phone="+8613800138000", client_ip="203.0.113.9"
        )
    )
    assert error.code == AuthErrorCode.SMS_RATE_LIMITED
    assert error.status_code == 429


def test_rotating_phones_from_one_ip_trips_the_ip_dimension() -> None:
    guard = _guard(phone_limit=100, ip_limit=2)
    guard.enforce(scope="sms:send", phone="+8613800138001", client_ip="203.0.113.9")
    guard.enforce(scope="sms:send", phone="+8613800138002", client_ip="203.0.113.9")
    # Every phone is fresh here, so only the IP counter can stop it.
    error = _limited(
        lambda: guard.enforce(
            scope="sms:send", phone="+8613800138003", client_ip="203.0.113.9"
        )
    )
    assert error.code == AuthErrorCode.SMS_RATE_LIMITED


def test_a_different_ip_still_gets_its_own_budget() -> None:
    guard = _guard(phone_limit=100, ip_limit=1)
    guard.enforce(scope="sms:send", phone="+8613800138004", client_ip="203.0.113.9")
    guard.enforce(scope="sms:send", phone="+8613800138005", client_ip="198.51.100.7")


def test_both_dimensions_return_the_same_code_and_message() -> None:
    phone_trip = _guard(phone_limit=1, ip_limit=100)
    phone_trip.enforce(scope="sms:send", phone="+8613800138006", client_ip="203.0.113.9")
    phone_error = _limited(
        lambda: phone_trip.enforce(
            scope="sms:send", phone="+8613800138006", client_ip="203.0.113.9"
        )
    )

    ip_trip = _guard(phone_limit=100, ip_limit=1)
    ip_trip.enforce(scope="sms:send", phone="+8613800138007", client_ip="203.0.113.9")
    ip_error = _limited(
        lambda: ip_trip.enforce(
            scope="sms:send", phone="+8613800138008", client_ip="203.0.113.9"
        )
    )

    assert (phone_error.code, phone_error.message, phone_error.status_code) == (
        ip_error.code,
        ip_error.message,
        ip_error.status_code,
    )
    # The wording must not say which counter tripped.
    lowered = phone_error.message.casefold()
    for leak in ("phone", "ip", "address", "number"):
        assert leak not in lowered


def test_spoofed_forwarded_for_is_ignored_without_a_trusted_proxy() -> None:
    trusted = parse_trusted_proxies("")
    assert (
        resolve_client_ip(
            peer="203.0.113.9",
            forwarded_for="198.51.100.7",
            trusted_proxies=trusted,
        )
        == "203.0.113.9"
    )
    # A spoofed header cannot buy a fresh IP budget: the peer is the same, so
    # the same counter keeps counting.
    guard = _guard(phone_limit=100, ip_limit=1)
    first_ip = resolve_client_ip(
        peer="203.0.113.9", forwarded_for="198.51.100.7", trusted_proxies=trusted
    )
    guard.enforce(scope="sms:send", phone="+8613800138009", client_ip=first_ip)
    second_ip = resolve_client_ip(
        peer="203.0.113.9", forwarded_for="192.0.2.55", trusted_proxies=trusted
    )
    assert second_ip == first_ip
    error = _limited(
        lambda: guard.enforce(
            scope="sms:send", phone="+8613800138010", client_ip=second_ip
        )
    )
    assert error.code == AuthErrorCode.SMS_RATE_LIMITED


def test_forwarded_for_is_used_only_behind_a_trusted_proxy() -> None:
    trusted = parse_trusted_proxies("10.0.0.0/8")
    assert (
        resolve_client_ip(
            peer="10.1.2.3", forwarded_for="198.51.100.7", trusted_proxies=trusted
        )
        == "198.51.100.7"
    )
    # Rightmost untrusted hop wins, so a client-prepended fake hop cannot
    # displace the address our proxy actually saw.
    assert (
        resolve_client_ip(
            peer="10.1.2.3",
            forwarded_for="1.2.3.4, 198.51.100.7",
            trusted_proxies=trusted,
        )
        == "198.51.100.7"
    )
    # An untrusted peer never gets its header honoured, trusted list or not.
    assert (
        resolve_client_ip(
            peer="203.0.113.9", forwarded_for="198.51.100.7", trusted_proxies=trusted
        )
        == "203.0.113.9"
    )


def test_garbage_forwarded_for_falls_back_to_the_peer() -> None:
    trusted = parse_trusted_proxies("10.0.0.0/8")
    for header in ("", "unknown", "not-an-ip", "[::1]:51999"):
        resolved = resolve_client_ip(
            peer="10.1.2.3", forwarded_for=header, trusted_proxies=trusted
        )
        assert resolved in {"10.1.2.3", "::1"}


def test_service_enforces_both_dimensions_through_one_guard() -> None:
    settings = _settings(sms_send_limit_per_hour=20, sms_ip_limit_per_hour=2)
    service = object.__new__(CredentialAuthService)
    service._settings = settings  # type: ignore[attr-defined]
    service._client_ip = "203.0.113.9"  # type: ignore[attr-defined]
    service._limiter = MemoryRateLimiter(  # type: ignore[attr-defined]
        clock=lambda: datetime(2026, 11, 11, tzinfo=UTC)
    )
    service._guard = AuthRateLimitGuard(  # type: ignore[attr-defined]
        limiter=service._limiter,  # type: ignore[attr-defined]
        phone_limits={
            "visual": 20,
            "sms:send": settings.sms_send_limit_per_hour,
            "sms:login": settings.sms_login_limit_per_hour,
        },
        ip_limit=settings.sms_ip_limit_per_hour,
    )
    service._enforce_rate_limit(scope="sms:send", phone_e164="+8613800138011")
    service._enforce_rate_limit(scope="sms:send", phone_e164="+8613800138012")
    error = _limited(
        lambda: service._enforce_rate_limit(  # type: ignore[attr-defined]
            scope="sms:send", phone_e164="+8613800138013"
        )
    )
    assert error.code == AuthErrorCode.SMS_RATE_LIMITED


def test_service_builds_the_guard_with_ip_budget_from_settings() -> None:
    source = (BACKEND / "app" / "service" / "identity" / "credential_auth.py").read_text(
        encoding="utf-8"
    )
    assert "AuthRateLimitGuard(" in source
    assert "ip_limit=settings.sms_ip_limit_per_hour" in source
    # No endpoint may touch the raw limiter any more: one guarded path only.
    assert "_limiter.hit(" not in source
    assert source.count("self._enforce_rate_limit(") == 3


def test_api_layer_passes_the_resolved_client_ip() -> None:
    api = (BACKEND / "app" / "api" / "v1" / "credential_auth.py").read_text(
        encoding="utf-8"
    )
    assert "client_ip=client_ip" in api
    dependency = (
        BACKEND / "app" / "api" / "dependencies" / "client_ip.py"
    ).read_text(encoding="utf-8")
    assert "trusted_proxies=parse_trusted_proxies(settings.trusted_proxy_cidrs)" in (
        dependency
    )


def test_dependency_ignores_a_spoofed_header_over_http() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies.client_ip import get_client_ip
    from app.core.config import get_settings

    app = FastAPI()

    @app.get("/probe")
    def probe(ip: str | None = Depends(get_client_ip)) -> dict[str, str | None]:
        return {"ip": ip}

    app.dependency_overrides[get_settings] = lambda: _settings(
        trusted_proxy_cidrs=""
    )
    with TestClient(app) as client:
        plain = client.get("/probe").json()["ip"]
        spoofed = client.get(
            "/probe", headers={"X-Forwarded-For": "198.51.100.7"}
        ).json()["ip"]
    assert plain == spoofed == "testclient"

