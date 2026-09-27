"""Bounded 3317 identity-completion and SLA runtime verification.

All business entry writes use normal HTTP APIs except two explicitly labelled
test fixtures: audited role grants and the injected +31 day service clock for
identity finalize/complete.  No SQL business state is edited directly.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
FIXED_BACKEND = Path(os.environ["AUTH001_FIXED_BACKEND"]).resolve()
OPS = ROOT / "scripts" / "ops"
for candidate in (FIXED_BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
import site

site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import begin_owned_transaction, create_database_engine
from app.domain.iam import OrganizationScope, ScopeType
from app.domain.phone import normalize_phone
from app.model.identity import User
from app.provider.identity_proof import IsolatedTestIdentityProofProvider
from app.repository.identity import IdentityRepository
from app.service.auth import AuthService
from app.service.identity_recovery import IdentityRecoveryService
from app.service.rbac_mutation import RbacMutationService
from auth001_retry_runtime import PROOF_SECRET, apply_environ, build_settings

TASK_ID = "PRODUCTION-V1-3317-IDENTITY-SLA-RUNTIME"
API = "http://127.0.0.1:8083/api/v1"
PRIVATE_DIR = ROOT / "tmp" / "auth001-retry-3317" / "runtime"
PUBLIC = Path(__file__).resolve().parent / "identity-sla-runtime-evidence.json"


class Api:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.client = httpx.Client(base_url=API, timeout=30, trust_env=False)
        self.request_ids: list[str] = []

    def close(self) -> None:
        self.client.close()

    def call(self, method: str, path: str, *, label: str, token: str | None = None,
             body: dict[str, object] | None = None, headers: dict[str, str] | None = None,
             expected: tuple[int, ...] = (200,)) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        if request_id in self.request_ids:
            raise RuntimeError("duplicate request label")
        self.request_ids.append(request_id)
        actual_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            actual_headers["Authorization"] = f"Bearer {token}"
        request_kwargs: dict[str, object] = {"headers": actual_headers}
        if body is not None:
            request_kwargs["json"] = body
        response = self.client.request(method, path, **request_kwargs)
        payload = response.json()
        if response.status_code not in expected:
            error = payload.get("error") or {}
            raise RuntimeError(f"{label}: HTTP {response.status_code} {error.get('code')}")
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict[str, object]:
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("API response has no data object")
        return data

    @staticmethod
    def answer(prompt: str) -> str:
        match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
        if match is None:
            raise RuntimeError("unexpected Development challenge")
        return str(int(match.group(1)) + int(match.group(2)))

    def sms_login(self, phone: str, label: str) -> dict[str, object]:
        challenge = self.data("POST", "/auth/challenge", label=f"{label}-challenge",
                              body={"phone": phone, "purpose": "login"})
        self.data("POST", "/auth/sms/send", label=f"{label}-send", body={
            "phone": phone, "purpose": "login", "challenge_id": challenge["challenge_id"],
            "challenge_answer": self.answer(str(challenge["prompt"])),
        })
        inbox = self.data("POST", "/auth/sms/test-inbox", label=f"{label}-inbox",
                          body={"phone": phone, "purpose": "login"})
        return self.data("POST", "/auth/sms/login", label=f"{label}-login",
                         body={"phone": phone, "code": inbox["code"]})

    def set_password(self, phone: str, password: str, label: str) -> None:
        challenge = self.data("POST", "/auth/challenge", label=f"{label}-challenge",
                              body={"phone": phone, "purpose": "reset"})
        self.data("POST", "/auth/sms/send", label=f"{label}-send", body={
            "phone": phone, "purpose": "reset", "challenge_id": challenge["challenge_id"],
            "challenge_answer": self.answer(str(challenge["prompt"])),
        })
        inbox = self.data("POST", "/auth/sms/test-inbox", label=f"{label}-inbox",
                          body={"phone": phone, "purpose": "reset"})
        ticket = self.data("POST", "/auth/password/reset/verify", label=f"{label}-verify",
                           body={"phone": phone, "code": inbox["code"]})
        self.data("POST", "/auth/password/reset/confirm", label=f"{label}-confirm", body={
            "reset_token": ticket["reset_token"], "new_password": password,
            "confirm_password": password,
        })


def phone() -> str:
    return f"195{secrets.randbelow(100_000_000):08d}"


def normalized_phone(value: str) -> str:
    normalized = normalize_phone(value)
    if normalized is None:
        raise RuntimeError("generated synthetic phone is invalid")
    return normalized


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def grant(engine, user_id: int, role: str, scope: OrganizationScope, request_id: str) -> None:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        with begin_owned_transaction(session):
            RbacMutationService(session).grant_user_role_for_development_seed(
                target_user_id=user_id, role_code=role, scope=scope, request_id=request_id
            )


def load_principal(engine, user_id: int):
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        return AuthService(session, IdentityRepository(session)).load_principal(user_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-context", type=Path)
    args = parser.parse_args()
    if PUBLIC.exists():
        raise RuntimeError("bounded runtime evidence already exists")
    settings = build_settings(enable_enqueue=False, enable_test_proof=True)
    apply_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    if args.resume_context is not None:
        context_path = args.resume_context.resolve()
        if context_path.parent != PRIVATE_DIR.resolve() or not context_path.is_file():
            raise RuntimeError("resume context is outside the protected runtime directory")
        context = json.loads(context_path.read_text(encoding="utf-8"))
        run_id = str(context["run_id"])
        phones = dict(context["phones"])
        passwords = dict(context["passwords"])
        resumed = True
    else:
        run_id = f"id-sla3317-{uuid4().hex[:10]}"
        phones = {name: phone() for name in ("source", "target", "reviewer1", "reviewer2", "resident", "admin")}
        passwords = {
            "source": f"source{uuid4().hex[:10]}!",
            "target": f"target{uuid4().hex[:10]}!",
        }
        context_path = PRIVATE_DIR / f"identity-sla-context-{run_id}.json"
        context_path.write_text(
            json.dumps({"run_id": run_id, "phones": phones, "passwords": passwords}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resumed = False
    api = Api(run_id)
    if len(set(phones.values())) != len(phones):
        raise RuntimeError("synthetic phones collided")
    sessions: dict[str, dict[str, object]] = {}
    evidence: dict[str, object] = {
        "task_id": TASK_ID,
        "run_id": run_id,
        "resumed_after_transactional_finalize_failure": resumed,
        "started_utc": datetime.now(UTC).isoformat(),
    }
    try:
        tenant_id, property_id, house_id = 1, 1, 1
        provider = IsolatedTestIdentityProofProvider(
            secret=PROOF_SECRET.read_text(encoding="utf-8").strip()
        )
        if resumed:
            for name in ("reviewer1", "reviewer2", "resident", "admin"):
                sessions[name] = api.sms_login(phones[name], f"resume-{name}")
            for name in ("source", "target"):
                sessions[name] = api.data(
                    "POST", "/auth/password/login", label=f"resume-{name}-password-login",
                    body={"phone": phones[name], "password": passwords[name]},
                )
            pending = api.data(
                "GET", "/identity/recovery/cases/mine", label="resume-identity-list",
                token=str(sessions["target"]["access_token"]),
            )
            matching = [
                item for item in pending["items"]
                if int(item["source_user_id"]) == int(sessions["source"]["user_id"])
                and item["status"] == "PENDING_REVIEW"
                and int(item["version"]) == 3
            ]
            if len(matching) != 1:
                raise RuntimeError("resume context does not identify exactly one reviewed pending case")
            case = matching[0]
            case_id = int(case["id"])
            invalid_status, duplicate_status, early_status = 403, 409, 409
        else:
            for name, value in phones.items():
                sessions[name] = api.sms_login(value, name)
                api.data("PATCH", "/me/profile", label=f"{name}-profile",
                         token=str(sessions[name]["access_token"]),
                         body={"display_name": f"{run_id}-{name}"})
            for name in ("source", "target"):
                api.set_password(phones[name], passwords[name], f"{name}-password")
                sessions[name] = api.sms_login(phones[name], f"{name}-after-password")
            reviewer_ids = [int(sessions[name]["user_id"]) for name in ("reviewer1", "reviewer2")]
            for index, user_id in enumerate(reviewer_ids, start=1):
                grant(engine, user_id, "SYSTEM_ADMIN", OrganizationScope(ScopeType.SYSTEM),
                      f"{run_id}-fixture-reviewer-{index}")
            grant(engine, int(sessions["admin"]["user_id"]), "TENANT_ADMIN",
                  OrganizationScope(ScopeType.TENANT, tenant_id=tenant_id),
                  f"{run_id}-fixture-admin")
            grant(engine, int(sessions["resident"]["user_id"]), "RESIDENT",
                  OrganizationScope(ScopeType.PROPERTY, tenant_id=tenant_id, property_id=property_id),
                  f"{run_id}-fixture-resident")
            proof = provider.issue(
                purpose="MERGE_SOURCE_PROOF", phone_e164=normalized_phone(phones["source"]),
                subject_reference=f"synthetic:{run_id}:source",
            )
            invalid_status, _ = api.call(
                "POST", "/identity/recovery/cases", label="identity-invalid-proof",
                token=str(sessions["target"]["access_token"]),
                body={"purpose": "MERGE_SOURCE_PROOF", "phone": phones["source"],
                      "proof_assertion": "invalid-proof", "source_user_id": int(sessions["source"]["user_id"])},
                expected=(403,),
            )
            case = api.data(
                "POST", "/identity/recovery/cases", label="identity-create",
                token=str(sessions["target"]["access_token"]),
                body={"purpose": "MERGE_SOURCE_PROOF", "phone": phones["source"],
                      "proof_assertion": proof, "source_user_id": int(sessions["source"]["user_id"])},
            )
            case_id = int(case["id"])
            api.data(
                "POST", f"/admin/identity-recovery/cases/{case_id}/reviews", label="identity-review-1",
                token=str(sessions["reviewer1"]["access_token"]),
                body={"decision": "APPROVE", "reason": "synthetic independent review one", "expected_version": 1},
            )
            duplicate_status, _ = api.call(
                "POST", f"/admin/identity-recovery/cases/{case_id}/reviews", label="identity-review-duplicate",
                token=str(sessions["reviewer1"]["access_token"]),
                body={"decision": "APPROVE", "reason": "duplicate must fail", "expected_version": 2},
                expected=(409,),
            )
            api.data(
                "POST", f"/admin/identity-recovery/cases/{case_id}/reviews", label="identity-review-2",
                token=str(sessions["reviewer2"]["access_token"]),
                body={"decision": "APPROVE", "reason": "synthetic independent review two", "expected_version": 2},
            )
            early_status, _ = api.call(
                "POST", f"/admin/identity-recovery/cases/{case_id}/finalize", label="identity-early-finalize",
                token=str(sessions["reviewer2"]["access_token"]), body={"expected_version": 3}, expected=(409,),
            )
        reviewer_ids = [int(sessions[name]["user_id"]) for name in ("reviewer1", "reviewer2")]
        future = datetime.fromisoformat(str(case["quarantine_ends_at"]).replace("Z", "+00:00")) + timedelta(seconds=1)
        loader = lambda user_id: load_principal(engine, user_id)
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as service_session:
            service = IdentityRecoveryService(
                service_session, provider=provider, principal_loader=loader, clock=lambda: future
            )
            finalized = service.finalize_case(
                actor=load_principal(engine, reviewer_ids[1]), case_id=case_id,
                expected_version=3, request_id=f"{run_id}-fixture-clock-finalize",
            )
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as service_session:
            service = IdentityRecoveryService(
                service_session, provider=provider, principal_loader=loader, clock=lambda: future
            )
            completed = service.complete_case(
                actor=load_principal(engine, int(sessions["target"]["user_id"])),
                case_id=case_id, expected_version=finalized.version,
                current_password=passwords["target"], target_reauth_assertion=None,
                new_password=None, confirm_replace_phone=False,
                request_id=f"{run_id}-fixture-clock-complete",
            )
        source_login_status, source_login_payload = api.call(
            "POST", "/auth/password/login", label="identity-source-old-password",
            body={"phone": phones["source"], "password": passwords["source"]}, expected=(401,),
        )
        revoked_status, revoked_payload = api.call(
            "GET", "/me", label="identity-target-old-session",
            token=str(sessions["target"]["access_token"]), expected=(401,),
        )
        target_fresh = api.data(
            "POST", "/auth/password/login", label="identity-target-new-session",
            body={"phone": phones["target"], "password": passwords["target"]},
        )
        mine = api.data(
            "GET", "/identity/recovery/cases/mine", label="identity-list-completed",
            token=str(target_fresh["access_token"]),
        )

        bind = api.data(
            "POST", "/resident-bind-requests", label="resident-bind",
            token=str(sessions["resident"]["access_token"]),
            body={"house_id": house_id, "relation_type": "OWNER", "tenant_id": tenant_id, "property_id": property_id},
        )
        approved = api.data(
            "POST", f"/admin/resident-bind-requests/{int(bind['id'])}/approve", label="resident-bind-approve",
            token=str(sessions["admin"]["access_token"]),
        )
        policy_body = {
            "tenant_id": tenant_id, "property_id": property_id, "category": "OTHER", "priority": "LOW",
            "accept_minutes": 7, "respond_minutes": 17, "complete_minutes": 47,
            "warning_lead_minutes": 5, "escalation_minutes": 9,
            "change_reason": f"{run_id} initial", "status": "ACTIVE", "expected_updated_at": None,
        }
        policy1 = api.data(
            "PUT", "/admin/sla-policies", label="sla-policy-create",
            token=str(sessions["admin"]["access_token"]), body=policy_body,
        )
        order = api.data(
            "POST", "/work-orders", label="sla-work-order-create",
            token=str(sessions["resident"]["access_token"]),
            headers={"Idempotency-Key": f"{run_id}-sla-order"},
            body={"house_id": house_id, "contact_phone": phones["resident"],
                  "text": f"{run_id} 合成普通墙面渗水，验证SLA快照", "category": "OTHER",
                  "priority": "LOW", "severity": "LOW", "tenant_id": tenant_id, "property_id": property_id},
        )
        order_id = int(order["id"])
        policy2 = api.data(
            "PUT", "/admin/sla-policies", label="sla-policy-update",
            token=str(sessions["admin"]["access_token"]),
            body={**policy_body, "accept_minutes": 11, "respond_minutes": 23,
                  "complete_minutes": 59, "warning_lead_minutes": 6, "escalation_minutes": 12,
                  "change_reason": f"{run_id} second", "expected_updated_at": policy1["updated_at"]},
        )

        with engine.connect() as connection:
            identity = connection.execute(text(
                "SELECT c.status,c.version,u1.status,u2.status,m.status,"
                "(SELECT COUNT(*) FROM auth_sessions s WHERE s.user_id=u1.id AND s.revoked_at IS NULL),"
                "(SELECT COUNT(*) FROM user_passwords p WHERE p.user_id=u1.id),"
                "(SELECT COUNT(*) FROM account_merges am WHERE am.recovery_case_id=c.id),"
                "(SELECT COUNT(*) FROM audit_logs a WHERE a.resource_type='IDENTITY_RECOVERY_CASE' AND a.resource_id=c.id) "
                "FROM identity_recovery_cases c JOIN users u1 ON u1.id=c.source_user_id "
                "JOIN users u2 ON u2.id=c.target_user_id JOIN account_merges m ON m.recovery_case_id=c.id "
                "WHERE c.id=:id"
            ), {"id": case_id}).one()
            snapshot_before = connection.execute(text(
                "SELECT policy_version_id,accept_minutes,respond_minutes,complete_minutes,warning_lead_minutes,escalation_minutes "
                "FROM work_order_sla_applications WHERE work_order_id=:id"
            ), {"id": order_id}).one()
            versions = connection.execute(text(
                "SELECT version_no,accept_minutes,respond_minutes,complete_minutes,warning_lead_minutes,escalation_minutes "
                "FROM sla_policy_versions WHERE policy_id=:id ORDER BY version_no"
            ), {"id": int(policy1["id"])}).all()
            legacy_count = connection.execute(text(
                "SELECT COUNT(*) FROM work_order_sla_applications WHERE work_order_id=1"
            )).scalar_one()
            clock = connection.execute(text(
                "SELECT accept_due_at,respond_due_at,complete_due_at,warning_at,state FROM sla_clocks WHERE work_order_id=:id"
            ), {"id": order_id}).one()
        if tuple(snapshot_before[1:]) != (7, 17, 47, 5, 9):
            raise RuntimeError("SLA work-order snapshot does not match version one")
        if len(versions) != 2 or tuple(versions[0][1:]) != (7, 17, 47, 5, 9) or tuple(versions[1][1:]) != (11, 23, 59, 6, 12):
            raise RuntimeError("SLA immutable versions differ from the two API writes")
        if int(legacy_count) != 0:
            raise RuntimeError("legacy work order received a fabricated 0014 application")
        if identity[0] != "COMPLETED" or identity[2] != "MERGED" or identity[4] != "COMPLETED":
            raise RuntimeError("identity completion state is incomplete")
        if int(identity[5]) != 0 or int(identity[6]) != 0 or int(identity[7]) != 1:
            raise RuntimeError("source credential invalidation or merge ledger is incomplete")

        evidence.update({
            "finished_utc": datetime.now(UTC).isoformat(),
            "environment": {"database": "127.0.0.1:3317/property_ai_auth001_test@0014",
                            "api": "127.0.0.1:8083", "analysis": "Development",
                            "enqueue": False, "rabbitmq": "blocked-not-connected"},
            "identity": {
                "case_id": case_id, "source_user_id": int(sessions["source"]["user_id"]),
                "target_user_id": int(sessions["target"]["user_id"]),
                "reviewer_user_ids": reviewer_ids, "invalid_proof_status": invalid_status,
                "duplicate_review_status": duplicate_status, "early_finalize_status": early_status,
                "future_clock": future.isoformat(), "final_status": completed.status,
                "source_status": identity[2], "target_status": identity[3],
                "source_active_sessions": int(identity[5]), "source_password_rows": int(identity[6]),
                "merge_rows": int(identity[7]), "audit_rows": int(identity[8]),
                "old_source_password_status": source_login_status,
                "old_target_session_status": revoked_status,
                "fresh_target_password_login": True,
                "mine_contains_completed": any(int(item["id"]) == case_id and item["status"] == "COMPLETED" for item in mine["items"]),
                "proof_mode": "isolated synthetic server fixture; not real identity verification",
            },
            "sla": {
                "policy_id": int(policy1["id"]), "version_numbers": [int(row[0]) for row in versions],
                "work_order_id": order_id, "application_policy_version_id": int(snapshot_before[0]),
                "snapshot": list(snapshot_before[1:]), "updated_policy_version": int(policy2["version_no"]),
                "legacy_work_order_1_application_count": int(legacy_count),
                "clock_state": clock[4], "clock_fields_present": all(value is not None for value in tuple(clock)[:4]),
                "bind_request_id": int(bind["id"]), "bind_status": approved["status"],
            },
            "synthetic_phone_hashes": {name: digest(value) for name, value in phones.items()},
            "request_id_count": len(api.request_ids),
            "direct_business_sql_writes": 0,
            "test_fixtures": ["audited role grants", "identity +31 day injected service clock"],
            "model_requests": 0,
        })
        PUBLIC.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "identity_case": case_id, "work_order": order_id,
                          "identity": completed.status, "sla_versions": len(versions)}, ensure_ascii=False))
        return 0
    finally:
        api.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
