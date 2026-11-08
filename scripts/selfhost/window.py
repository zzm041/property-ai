"""One-use, durable admission budget for a bounded online demonstration."""
import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time

from .runner import RuntimeFailure


@dataclass(frozen=True)
class WindowSpec:
    window_id: str
    max_attempts: int
    expires_at: float
    record: Path

    @classmethod
    def from_dict(cls, value, base):
        try:
            data = dict(value)
            data["record"] = (Path(base) / data["record"]).resolve()
            result = cls(**data)
            if not re.fullmatch(r"[a-z0-9-]{3,40}", result.window_id):
                raise ValueError()
            if type(result.max_attempts) is not int or not 1 <= result.max_attempts <= 3:
                raise ValueError()
            if type(result.expires_at) not in (float, int) or not math.isfinite(result.expires_at):
                raise ValueError()
            if not 0 < result.expires_at - time.time() <= 1800:
                raise ValueError()
            return result
        except (TypeError, KeyError, ValueError):
            raise ValueError("WINDOW_CONFIG_INVALID") from None

    @classmethod
    def read(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)


class WindowRunner:
    def __init__(self, runner, spec):
        self.runner, self.spec = runner, spec
        self.used = 0
        self.failed = self.closed = False
        remaining = spec.expires_at - time.time()
        if not 0 < remaining <= 1800:
            raise ValueError("WINDOW_CONFIG_INVALID")
        self.deadline = time.monotonic() + remaining
        try:
            self.fd = os.open(spec.record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError("WINDOW_ALREADY_CLAIMED") from None
        try:
            self._record({"event": "claimed", "window_id": spec.window_id,
                          "max_attempts": spec.max_attempts, "expires_at": spec.expires_at})
        except BaseException:
            os.close(self.fd)
            self.closed = True
            raise

    @property
    def faulted(self):
        return self.failed or self.closed or self.runner.faulted

    def remaining(self):
        # A backwards wall-clock change cannot extend the original window.
        return min(self.deadline - time.monotonic(), self.spec.expires_at - time.time())

    def _record(self, value):
        try:
            raw = (json.dumps(value, separators=(",", ":")) + "\n").encode("ascii")
            while raw:
                written = os.write(self.fd, raw)
                if not written:
                    raise OSError()
                raw = raw[written:]
            os.fsync(self.fd)
        except OSError:
            self.failed = True
            raise RuntimeFailure("WINDOW_RECORD_FAILED") from None

    async def run(self, kind, value):
        if self.remaining() <= 0:
            raise RuntimeFailure("WINDOW_EXPIRED")
        if self.faulted:
            raise RuntimeFailure("WINDOW_UNAVAILABLE")
        if self.used >= self.spec.max_attempts:
            raise RuntimeFailure("WINDOW_EXHAUSTED", 429)
        # No await before durable reservation: concurrent admissions cannot share a slot.
        self.used += 1
        self._record({"event": "reserved", "attempt": self.used, "kind": kind, "at": time.time()})
        operation = asyncio.create_task(self.runner.run(kind, value))
        try:
            while self.remaining() > 0:
                done, _ = await asyncio.wait({operation}, timeout=min(.25, max(0, self.remaining())))
                if self.remaining() <= 0:
                    break
                if done:
                    return operation.result()
            raise RuntimeFailure("WINDOW_EXPIRED", 503)
        finally:
            if not operation.done():
                operation.cancel()
            # Await the underlying runner's owned-process cleanup, also on caller cancellation.
            await asyncio.gather(operation, return_exceptions=True)

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.fd)
