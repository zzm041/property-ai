"""Supervised durable task-intent publisher for the deployment artifact."""
from __future__ import annotations

import argparse
import signal
import threading

from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.worker.task_intents import run_publisher


def serve(*, seconds: int = 3600, settings: Settings | None = None) -> None:
    active = settings or get_settings()
    if not active.durable_tasks_enabled:
        raise RuntimeError("DURABLE_TASKS_ENABLED is required for the task-intent publisher")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)
    engine = create_database_engine(active.active_database_url)
    try:
        from app.worker.healthcheck import touch_publisher_heartbeat

        run_publisher(
            engine, active, ids_supplier=lambda: None, seconds=seconds, stop=stop,
            heartbeat=touch_publisher_heartbeat,
        )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded durable task-intent publisher")
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args()
    serve(seconds=args.seconds)


if __name__ == "__main__":
    main()
