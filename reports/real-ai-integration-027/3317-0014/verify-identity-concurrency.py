"""Verify duplicate concurrent completion on the completed synthetic merge.

This creates no new business object. Each contender has its own real MySQL
session; the locked completed case must remain a single immutable result.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import site
import sys
from threading import Barrier

ROOT = Path(__file__).resolve().parents[3]
FIXED_BACKEND = Path(os.environ["AUTH001_FIXED_BACKEND"]).resolve()
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
sys.path[:0] = [str(FIXED_BACKEND), str(ROOT), str(ROOT / "scripts" / "ops")]

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import create_database_engine
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.identity_recovery import IdentityRecoveryService
from auth001_retry_runtime import apply_environ, build_settings

SOURCE = Path(__file__).resolve().parent / "identity-sla-runtime-evidence.json"
OUTPUT = Path(__file__).resolve().parent / "identity-concurrency-evidence.json"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError("concurrency evidence already exists")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    case_id = int(source["identity"]["case_id"])
    target_user_id = int(source["identity"]["target_user_id"])
    settings = build_settings(enable_enqueue=False, enable_test_proof=True)
    apply_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    barrier = Barrier(2)

    def principal_loader(user_id: int):
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as principal_session:
            return AuthService(
                principal_session, IdentityRepository(principal_session)
            ).load_principal(user_id)

    def contender(index: int) -> dict[str, object]:
        actor = principal_loader(target_user_id)
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            result = IdentityRecoveryService(
                session, principal_loader=principal_loader
            ).complete_case(
                actor=actor,
                case_id=case_id,
                expected_version=1,
                current_password=None,
                target_reauth_assertion=None,
                new_password=None,
                confirm_replace_phone=False,
                request_id=f"{source['run_id']}-concurrent-repeat-{index}",
            )
            return {"contender": index, "case_id": result.id, "status": result.status, "version": result.version}

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contender, (1, 2)))
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT c.status,c.version,u1.status,u2.status,"
                "(SELECT COUNT(*) FROM account_merges m WHERE m.recovery_case_id=c.id),"
                "(SELECT COUNT(*) FROM audit_logs a WHERE a.resource_type='IDENTITY_RECOVERY_CASE' "
                "AND a.resource_id=c.id AND a.action='IDENTITY_RECOVERY_COMPLETED') "
                "FROM identity_recovery_cases c JOIN users u1 ON u1.id=c.source_user_id "
                "JOIN users u2 ON u2.id=c.target_user_id WHERE c.id=:id"
            ), {"id": case_id}).one()
        if any(item["status"] != "COMPLETED" for item in results):
            raise RuntimeError("a repeated completion did not observe COMPLETED")
        if tuple(row) != ("COMPLETED", results[0]["version"], "MERGED", "ACTIVE", 1, 1):
            raise RuntimeError("concurrent repeat changed the immutable completion result")
        evidence = {
            "task_id": "PRODUCTION-V1-3317-IDENTITY-CONCURRENCY",
            "run_id": source["run_id"],
            "database": "127.0.0.1:3317/property_ai_auth001_test@0014",
            "case_id": case_id,
            "contenders": results,
            "final": {
                "case_status": row[0], "case_version": int(row[1]),
                "source_status": row[2], "target_status": row[3],
                "account_merge_rows": int(row[4]), "completion_audit_rows": int(row[5]),
            },
            "new_business_objects": 0,
            "model_requests": 0,
        }
        OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence["final"], ensure_ascii=False))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
