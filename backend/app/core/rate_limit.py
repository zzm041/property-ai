"""Injectable auth rate limiter. Redis implementation fails closed."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


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
