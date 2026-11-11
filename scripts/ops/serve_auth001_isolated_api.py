"""Serve the isolated AUTH-IDENTITY API on 8083. Does not drop the exclusive schema."""

from __future__ import annotations

import os
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

import uvicorn

from auth001_isolated_runtime import (
    ISOLATED_API_PORT,
    apply_isolated_environ,
    build_isolated_settings,
    describe_isolated_target,
)


def main() -> None:
    settings = build_isolated_settings(enable_enqueue=True)
    # Configure the publisher before importing app.main. Importing the router can
    # import Celery tasks, whose process-global app reads its broker only once.
    apply_isolated_environ(settings)
    from app.main import create_app

    print(describe_isolated_target(settings), flush=True)
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=ISOLATED_API_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
