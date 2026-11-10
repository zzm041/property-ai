"""Container health probes for the Celery worker and the task-intent publisher.

PA-104-D: both containers shipped `healthcheck: disable: true`, so a wedged
worker or a stalled publisher stayed "up" for Docker and for anything reading
container state. These probes answer real questions:

* ``worker`` — is a live Celery worker consuming from the configured queue?
* ``publisher`` — is the publisher loop still ticking (heartbeat freshness) and
  is the broker reachable from inside the container?

Exit code 0 means healthy; anything else makes Docker mark the container
unhealthy. Nothing here mutates data.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from app.core.config import Settings, get_settings
from app.worker.celery_app import celery_app

PUBLISHER_HEARTBEAT_ENV = "PUBLISHER_HEARTBEAT_FILE"
DEFAULT_PUBLISHER_HEARTBEAT = "/tmp/property-ai-publisher-heartbeat"
PUBLISHER_STALE_SECONDS = 60


def publisher_heartbeat_path() -> Path:
    return Path(os.environ.get(PUBLISHER_HEARTBEAT_ENV) or DEFAULT_PUBLISHER_HEARTBEAT)


def touch_publisher_heartbeat() -> None:
    """Called by the publisher loop; a missing file means the loop never ran."""

    path = publisher_heartbeat_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        # A read-only /tmp must not take the publisher down.
        pass


def _probe_worker(settings: Settings) -> tuple[bool, str]:
    replies = celery_app.control.inspect(timeout=max(1, int(settings.readiness_timeout_seconds))).ping()
    if not replies:
        return False, "no Celery worker answered ping"
    return True, f"workers={len(replies)}"


def _probe_publisher(settings: Settings) -> tuple[bool, str]:
    path = publisher_heartbeat_path()
    if not path.exists():
        return False, f"heartbeat missing at {path}"
    age = time.time() - path.stat().st_mtime
    if age > PUBLISHER_STALE_SECONDS:
        return False, f"heartbeat stale ({age:.0f}s > {PUBLISHER_STALE_SECONDS}s)"
    from app.core.readiness import probe_rabbitmq

    probe_rabbitmq(settings)
    return True, f"heartbeat age={age:.0f}s broker=up"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker/publisher container health probe")
    parser.add_argument("role", choices=("worker", "publisher"))
    args = parser.parse_args(argv)
    settings = get_settings()
    probe = _probe_worker if args.role == "worker" else _probe_publisher
    try:
        healthy, detail = probe(settings)
    except Exception as exc:  # noqa: BLE001 - any probe failure is unhealthiness
        print(f"unhealthy role={args.role} error={type(exc).__name__}: {exc}")
        return 1
    print(f"{'healthy' if healthy else 'unhealthy'} role={args.role} {detail}")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
