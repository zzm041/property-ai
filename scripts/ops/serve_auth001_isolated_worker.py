"""Exclusive AUTH001 Celery worker. Consumes only property_ai.auth001."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
os.chdir(BACKEND)

from auth001_isolated_runtime import (
    ISOLATED_QUEUE,
    apply_isolated_environ,
    build_isolated_settings,
    describe_isolated_target,
)


def main() -> int:
    settings = build_isolated_settings(enable_enqueue=True)
    apply_isolated_environ(settings)
    print(describe_isolated_target(settings), flush=True)
    print(f"worker_queue={ISOLATED_QUEUE} pool=solo", flush=True)
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker.celery_app:celery_app",
            "worker",
            "-Q",
            ISOLATED_QUEUE,
            "--pool=solo",
            "--loglevel=info",
            "--hostname=auth001@%h",
        ],
        cwd=str(BACKEND),
        env=os.environ.copy(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
