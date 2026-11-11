"""Injectable auth rate limiter. Redis implementation fails closed.

Rate limiting for the SMS/password surface carries two independent dimensions:

* a per-phone budget, so one number cannot be spammed, and
* a per-client-IP budget, so rotating phone numbers from one host cannot
  bypass the first one.

Both dimensions answer with the same error code and the same wording: a caller
that gets limited learns "slow down", never which counter tripped.

The client IP is only taken from ``X-Forwarded-For`` when the socket peer is a
configured trusted proxy. Anywhere else the header is attacker-controlled and
is ignored outright.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

RATE_LIMIT_WINDOW_SECONDS = 3600


class RateLimiter:
    def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        raise NotImplementedError


@dataclass
class MemoryRateLimiter(RateLimiter):
    """In-process limiter for unit tests. Does not use Redis."""

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _hits: dict[str, list[datetime]] = field(default_factory=dict)

    def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = self.clock()
        window_start = now - timedelta(seconds=window_seconds)
        stamps = [item for item in self._hits.get(key, ()) if item > window_start]
        if len(stamps) >= limit:
            self._hits[key] = stamps
            return False
        stamps.append(now)
        self._hits[key] = stamps
        return True


class RedisRateLimiter(RateLimiter):
    _INCR_EXPIRE = (
        "local n = redis.call('INCR', KEYS[1]) "
        "if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
        "return n"
    )

    def __init__(self, redis_url: str, *, timeout_seconds: float = 1.0) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds

    def hit(self, key: str, *, limit: int, window_seconds: int) -> bool:
        import redis

        try:
            client = redis.Redis.from_url(
                self._redis_url,
                socket_timeout=self._timeout_seconds,
            )
            count = int(
                client.eval(self._INCR_EXPIRE, 1, key, int(window_seconds))
            )
            return count <= limit
        except redis.RedisError:
            return False


def rate_limited_error() -> Exception:
    """One error for both dimensions, so the trigger is not observable."""

    from app.core.errors import AppError, AuthErrorCode

    return AppError(
        code=AuthErrorCode.SMS_RATE_LIMITED,
        message="Too many requests. Please try again later.",
        status_code=429,
    )


def parse_trusted_proxies(raw: str | Iterable[str] | None) -> tuple[ipaddress._BaseNetwork, ...]:
    """Parse a comma separated proxy allow-list; blank or bad entries are dropped."""

    if raw is None:
        return ()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    networks: list[ipaddress._BaseNetwork] = []
    for item in items:
        candidate = str(item).strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = str(value).strip().strip('"')
    if not candidate or candidate.casefold() == "unknown":
        return None
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1 and "." in candidate:
        # IPv4 with a port, e.g. "203.0.113.9:51234".
        host, _, port = candidate.partition(":")
        if port.isdigit():
            candidate = host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _is_trusted(ip: str, trusted: tuple[ipaddress._BaseNetwork, ...]) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in trusted)


def resolve_client_ip(
    *,
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: tuple[ipaddress._BaseNetwork, ...] | str = (),
) -> str | None:
    """Return the caller's IP, honouring X-Forwarded-For only behind a trusted proxy.

    Everything left of the rightmost untrusted hop is client supplied, so that
    hop is the closest thing to a real client address. Without a trusted peer
    the header is ignored completely - it is attacker-controlled there.
    """

    networks = (
        parse_trusted_proxies(trusted_proxies)
        if isinstance(trusted_proxies, str)
        else tuple(trusted_proxies)
    )
    peer_ip = normalize_ip(peer)
    if not peer_ip:
        # Opaque peer (unix socket, in-process test client). It still shares one
        # bucket, and the forwarded header stays ignored.
        opaque = (peer or "").strip()
        return opaque or None
    if not networks or not _is_trusted(peer_ip, networks):
        return peer_ip

    hops = [
        hop
        for hop in (normalize_ip(part) for part in (forwarded_for or "").split(","))
        if hop is not None
    ]
    if not hops:
        return peer_ip
    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop
    return hops[0]


@dataclass
class AuthRateLimitGuard:
    """Two-dimensional (phone + IP) limiter for the credential endpoints."""

    limiter: RateLimiter
    phone_limits: Mapping[str, int]
    ip_limit: int
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS

    def enforce(self, *, scope: str, phone: str, client_ip: str | None) -> None:
        phone_limit = self.phone_limits.get(scope)
        if phone_limit is None:
            raise KeyError(f"unknown rate limit scope: {scope}")
        phone_allowed = self.limiter.hit(
            f"{scope}:phone:{phone}",
            limit=phone_limit,
            window_seconds=self.window_seconds,
        )
        ip_allowed = True
        if client_ip:
            ip_allowed = self.limiter.hit(
                f"{scope}:ip:{client_ip}",
                limit=self.ip_limit,
                window_seconds=self.window_seconds,
            )
        # Both counters are always incremented, then a single indistinguishable
        # error answers: which dimension tripped stays server-side.
        if not phone_allowed or not ip_allowed:
            raise rate_limited_error()
