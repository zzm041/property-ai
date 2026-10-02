"""Launch the fixed 3317 API, Worker, bounded Beat, or exact-ID publisher."""
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

from auth001_retry_runtime import (
    API_PORT,
    SLA_ACCEPTANCE_QUEUE,
    apply_environ,
    build_settings,
    parse_identity_test_clock,
    safe_description,
)


def _install_identity_test_clock(application, settings) -> None:
    fixed_clock = parse_identity_test_clock(
        settings,
        os.environ.get("AUTH001_IDENTITY_TEST_CLOCK_UTC"),
    )
    if fixed_clock is None:
        return

    from fastapi import Depends

    from app.api.dependencies.db import get_db_session
    from app.api.v1.identity_recovery import get_identity_recovery_service
    from app.provider.identity_proof import IsolatedTestIdentityProofProvider
    from app.repository.identity import IdentityRepository
    from app.service.auth import AuthService
    from app.service.identity_recovery import IdentityRecoveryService

    def fixed_service(session=Depends(get_db_session)) -> IdentityRecoveryService:
        provider = IsolatedTestIdentityProofProvider(
            secret=settings.identity_test_proof_secret.get_secret_value(),
            clock=lambda: fixed_clock,
        )
        return IdentityRecoveryService(
            session,
            provider=provider,
            principal_loader=AuthService(
                session, IdentityRepository(session)
            ).load_principal,
            clock=lambda: fixed_clock,
        )

    application.dependency_overrides[get_identity_recovery_service] = fixed_service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=("api", "worker", "beat", "publisher"))
    parser.add_argument("--intent-ids-file", type=Path)
    parser.add_argument("--work-order-ids-file", type=Path)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    if args.component == "publisher" and args.intent_ids_file is None:
        parser.error("publisher requires an exact intent ID file")
    if args.component == "beat" and args.work_order_ids_file is None:
        parser.error("beat requires an exact work-order ID file")
    settings = build_settings(
        enable_enqueue=args.component != "api",
        enable_test_proof=True,
        # The API may enqueue notification-delivery work even when AI analysis
        # enqueueing is disabled.  Keep every acceptance-run message off the
        # protected imported queue, not only Worker/Beat traffic.
        queue_name=SLA_ACCEPTANCE_QUEUE,
    )
    apply_environ(settings)
    print(json.dumps({"component": args.component, **safe_description(settings)}, ensure_ascii=False), flush=True)
    if args.component == "api":
        import uvicorn
        from app.main import create_app

        application = create_app(settings)
        _install_identity_test_clock(application, settings)
        uvicorn.run(application, host="127.0.0.1", port=API_PORT, log_level="warning")
        return 0
    if args.component == "worker":
        from app.worker.celery_app import celery_app

        celery_app.worker_main([
            "worker", "-Q", settings.resolved_celery_queue,
            "--pool=solo", "--loglevel=warning",
            "--hostname=auth001-retry-3317@%h", "--without-mingle", "--without-gossip",
        ])
        return 0
    if args.component == "beat":
        ids = json.loads(args.work_order_ids_file.read_text(encoding="utf-8"))
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 4
            or not all(type(item) is int and item > 0 for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise RuntimeError("beat exact work-order IDs are invalid")
        from app.worker.celery_app import celery_app

        celery_app.conf.beat_schedule = {
            f"bounded-sla-sweep-{work_order_id}": {
                "task": "app.worker.tasks.sweep_sla_task",
                "schedule": settings.sla_sweep_interval_seconds,
                "args": (work_order_id,),
                "options": {"queue": settings.resolved_celery_queue},
            }
            for work_order_id in ids
        }
        schedule = ROOT / "tmp" / "auth001-retry-3317" / "runtime" / "celerybeat-schedule"
        celery_app.Beat(
            loglevel="WARNING",
            schedule=str(schedule),
        ).run()
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
