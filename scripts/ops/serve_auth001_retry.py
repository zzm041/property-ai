"""Launch the fixed 3317 API, Worker, or bounded exact-ID publisher."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
FIXED_BACKEND = Path(os.environ["AUTH001_FIXED_BACKEND"]).resolve()
if str(FIXED_BACKEND) not in sys.path:
    sys.path.insert(0, str(FIXED_BACKEND))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(1, str(Path(__file__).resolve().parent))
os.chdir(FIXED_BACKEND)

from auth001_retry_runtime import API_PORT, QUEUE, apply_environ, build_settings, safe_description


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=("api", "worker", "publisher"))
    parser.add_argument("--intent-ids-file", type=Path)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    if args.component == "publisher" and args.intent_ids_file is None:
        parser.error("publisher requires an exact intent ID file")
    settings = build_settings(
        enable_enqueue=args.component != "api",
        enable_test_proof=True,
    )
    apply_environ(settings)
    print(json.dumps({"component": args.component, **safe_description(settings)}, ensure_ascii=False), flush=True)
    if args.component == "api":
        import uvicorn
        from app.main import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=API_PORT, log_level="warning")
        return 0
    if args.component == "worker":
        from app.worker.celery_app import celery_app

        celery_app.worker_main([
            "worker", "-Q", QUEUE, "--pool=solo", "--loglevel=warning",
            "--hostname=auth001-retry-3317@%h", "--without-mingle", "--without-gossip",
        ])
        return 0
    ids = json.loads(args.intent_ids_file.read_text(encoding="utf-8"))
    if not isinstance(ids, list) or not all(type(item) is int and item > 0 for item in ids):
        raise RuntimeError("publisher exact intent IDs are invalid")
    from app.core.database import create_database_engine
    from app.worker.task_intents import run_publisher

    engine = create_database_engine(settings.active_database_url)
    try:
        run_publisher(engine, settings, ids_supplier=lambda: tuple(ids), seconds=args.seconds)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
