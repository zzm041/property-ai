"""Prepare only synthetic prerequisites for real mini-program submit evidence.

The identity case, reviews, finalize, completion, SLA policy and work order are
deliberately not written here.  Those business writes are reserved for the
Developer Tools page runner.
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
from sqlalchemy.orm import Session

from app.core.database import begin_owned_transaction, create_database_engine
from app.domain.iam import OrganizationScope, ScopeType
from app.domain.phone import normalize_phone
from app.provider.identity_proof import IsolatedTestIdentityProofProvider
from app.service.rbac_mutation import RbacMutationService
from auth001_retry_runtime import PROOF_SECRET, apply_environ, build_settings

API = "http://127.0.0.1:8083/api/v1"
PRIVATE_DIR = ROOT / "tmp" / "auth001-retry-3317" / "runtime"
PUBLIC_BASE = Path(__file__).resolve().parent / "page-submit-preparation.json"


class Api:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.client = httpx.Client(base_url=API, timeout=30, trust_env=False)
        self.labels: set[str] = set()

    def close(self) -> None:
        self.client.close()

    def data(self, method: str, path: str, *, label: str, token: str | None = None,
             body: dict[str, object] | None = None) -> dict[str, object]:
        if label in self.labels:
            raise RuntimeError("duplicate preparation request label")
        self.labels.add(label)
        headers = {"X-Request-Id": f"{self.run_id}-{label}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.client.request(method, path, headers=headers, json=body)
        payload = response.json()
        if response.status_code != 200 or not isinstance(payload.get("data"), dict):
            error = payload.get("error") or {}
            raise RuntimeError(f"{label}: HTTP {response.status_code} {error.get('code')}")
        return payload["data"]

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


def new_phone() -> str:
    return f"195{secrets.randbelow(100_000_000):08d}"


def normalized(value: str) -> str:
    result = normalize_phone(value)
    if result is None:
        raise RuntimeError("synthetic phone is invalid")
    return result


def grant_system_admin(engine, user_id: int, request_id: str) -> None:
    with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
        with begin_owned_transaction(session):
            RbacMutationService(session).grant_user_role_for_development_seed(
                target_user_id=user_id,
                role_code="SYSTEM_ADMIN",
                scope=OrganizationScope(ScopeType.SYSTEM),
                request_id=request_id,
            )


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-context", type=Path, required=True)
    parser.add_argument("--attempt", type=int, default=1, choices=range(1, 4))
    args = parser.parse_args()
    public = (
        PUBLIC_BASE
        if args.attempt == 1
        else PUBLIC_BASE.with_name(f"page-submit-preparation-{args.attempt}.json")
    )
    if public.exists():
        raise RuntimeError("page-submit preparation evidence already exists")
    base_context = args.base_context.resolve()
    if base_context.parent != PRIVATE_DIR.resolve() or not base_context.is_file():
        raise RuntimeError("base context is outside the protected runtime directory")
    base = json.loads(base_context.read_text(encoding="utf-8"))
    if not all(name in base.get("phones", {}) for name in ("admin", "resident")):
        raise RuntimeError("base context lacks the accepted SLA page identities")

    settings = build_settings(enable_enqueue=False, enable_test_proof=True)
    apply_environ(settings)
    engine = create_database_engine(settings.active_database_url)
    run_id = f"id-sla-page{args.attempt}-{uuid4().hex[:10]}"
    phones = {name: new_phone() for name in ("source", "target", "reviewer1", "reviewer2")}
    if len(set(phones.values())) != len(phones):
        raise RuntimeError("synthetic phones collided")
    passwords = {
        "source": f"source{uuid4().hex[:10]}!",
        "target": f"target{uuid4().hex[:10]}!",
    }
    past_clock = datetime.now(UTC) - timedelta(days=31, minutes=5)
    context_path = PRIVATE_DIR / f"page-submit-context-{run_id}.json"
    api = Api(run_id)
    try:
        sessions = {name: api.sms_login(phone, name) for name, phone in phones.items()}
        for name in ("source", "target"):
            api.set_password(phones[name], passwords[name], f"{name}-password")
        for index, name in enumerate(("reviewer1", "reviewer2"), start=1):
            grant_system_admin(
                engine,
                int(sessions[name]["user_id"]),
                f"{run_id}-fixture-reviewer-{index}",
            )
        provider = IsolatedTestIdentityProofProvider(
            secret=PROOF_SECRET.read_text(encoding="utf-8").strip(),
            clock=lambda: past_clock,
        )
        proof = provider.issue(
            purpose="MERGE_SOURCE_PROOF",
            phone_e164=normalized(phones["source"]),
            subject_reference=f"synthetic:{run_id}:source",
        )
        private = {
            "run_id": run_id,
            "past_clock_utc": past_clock.isoformat(),
            "phones": {
                **phones,
                "admin": base["phones"]["admin"],
                "resident": base["phones"]["resident"],
            },
            "passwords": passwords,
            "proof_assertion": proof,
            "user_ids": {name: int(session["user_id"]) for name, session in sessions.items()},
            "case_id": None,
            "sla_policy_id": None,
            "work_order_id": None,
        }
        context_path.write_text(json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8")
        public.write_text(json.dumps({
            "task_id": "PRODUCTION-V1-3317-IDENTITY-SLA-PAGE-SUBMIT",
            "run_id": run_id,
            "prepared_utc": datetime.now(UTC).isoformat(),
            "private_context": str(context_path.relative_to(ROOT)),
            "past_clock_utc": past_clock.isoformat(),
            "synthetic_user_ids": private["user_ids"],
            "phone_hashes": {name: short_hash(phone) for name, phone in phones.items()},
            "proof_assertion": "server-generated isolated fixture; value omitted",
            "business_writes_reserved_for_page": [
                "identity case", "two reviews", "finalize", "complete",
                "SLA policy", "work order",
            ],
            "boundaries": {
                "database": "127.0.0.1:3317/property_ai_auth001_test@0014",
                "enqueue": False,
                "real_identity_provider_claimed": False,
                "direct_identity_or_sla_business_sql": False,
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "context": str(context_path)}, ensure_ascii=False))
        return 0
    finally:
        api.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
