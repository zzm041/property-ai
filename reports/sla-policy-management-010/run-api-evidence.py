"""Exercise scoped SLA management through normal AUTH001 sessions and APIs.

The script refuses to start when private run state already exists.  All product
mutations use HTTP APIs.  The sole fixture-only mutation creates a fresh,
normally authenticated Property Admin grant through the existing audited
Development fixture service.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.core.database import (  # noqa: E402
    create_database_engine,
    run_owned_transaction,
)
from app.domain.iam import OrganizationScope, ScopeType  # noqa: E402
from app.service.rbac_mutation import RbacMutationService  # noqa: E402
from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "SLA-POLICY-MGMT-010"
API = "http://127.0.0.1:8083/api/v1"
SOURCE_CONTEXT = REPO / "tmp" / "auth-password-change" / "context.json"
PRIVATE_DIR = REPO / "tmp" / "sla-policy-management-010"
PRIVATE_STATE = PRIVATE_DIR / "context.json"
PUBLIC_EVIDENCE = Path(__file__).resolve().parent / "api-runtime-evidence.json"
API_CATEGORY = "ELECTRICAL"
API_PRIORITY = "HIGH"
PAGE_CATEGORY = "OTHER"
PAGE_PRIORITY = "LOW"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _phone() -> str:
    return f"193{secrets.randbelow(100_000_000):08d}"


def _challenge_answer(prompt: str) -> str:
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*", prompt)
    if match is None:
        raise RuntimeError("Unsupported Development challenge format")
    return str(int(match.group(1)) + int(match.group(2)))


def _save_private(state: dict[str, object]) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class Client:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.http = httpx.Client(base_url=API, timeout=30.0)
        self.request_ids: dict[str, str] = {}

    def close(self) -> None:
        self.http.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        label: str,
        token: str | None = None,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, object]]:
        request_id = f"{self.run_id}-{label}"
        if label in self.request_ids:
            raise RuntimeError(f"Duplicate request label: {label}")
        self.request_ids[label] = request_id
        request_headers = {"X-Request-Id": request_id, **(headers or {})}
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        response = self.http.request(
            method,
            path,
            headers=request_headers,
            json=body if body is not None else None,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} returned non-JSON HTTP {response.status_code}") from exc
        if response.status_code not in expected:
            error = payload.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            raise RuntimeError(f"{label} failed: HTTP {response.status_code} code={code}")
        return response.status_code, payload

    def data(self, *args, **kwargs) -> dict[str, object]:
        _status, payload = self.call(*args, **kwargs)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"{kwargs.get('label', 'request')} returned no data object")
        return data

    def sms_login(self, phone: str, label: str) -> dict[str, object]:
        challenge = self.data(
            "POST",
            "/auth/challenge",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-challenge",
        )
        self.data(
            "POST",
            "/auth/sms/send",
            body={
                "phone": phone,
                "purpose": "login",
                "challenge_id": challenge["challenge_id"],
                "challenge_answer": _challenge_answer(str(challenge["prompt"])),
            },
            label=f"{label}-sms-send",
        )
        inbox = self.data(
            "POST",
            "/auth/sms/test-inbox",
            body={"phone": phone, "purpose": "login"},
            label=f"{label}-sms-inbox",
        )
        return self.data(
            "POST",
            "/auth/sms/login",
            body={"phone": phone, "code": inbox["code"]},
            label=f"{label}-sms-login",
        )


def _error_code(payload: dict[str, object]) -> str | None:
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _guard(settings) -> None:
    database = make_url(settings.active_database_url)
    broker = urlsplit(settings.celery_broker_url)
    actual = (
        database.host,
        database.port,
        database.database,
        settings.app_env,
        settings.celery_task_queue,
        broker.path.lstrip("/"),
    )
    expected = (
        "127.0.0.1",
        3307,
        "property_ai_auth001_test",
        "test",
        ISOLATED_QUEUE,
        ISOLATED_VHOST,
    )
    if actual != expected:
        raise RuntimeError(f"AUTH001 target guard rejected runtime settings: {actual}")


def _assert_source_fixture(engine, source: dict[str, object]) -> None:
    org = source["org"]
    sessions = source["sessions"]
    user_ids = [int(sessions[name]["user_id"]) for name in ("primary", "secondary", "manager", "staff")]
    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        users = connection.execute(
            text("SELECT COUNT(*) FROM users WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": user_ids},
        ).scalar_one()
        property_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM properties "
                "WHERE id = :property_id AND tenant_id = :tenant_id"
            ),
            {
                "property_id": int(org["property_id"]),
                "tenant_id": int(org["tenant_id"]),
            },
        ).scalar_one()
        house_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM houses "
                "WHERE id = :house_id AND property_id = :property_id "
                "AND tenant_id = :tenant_id"
            ),
            {
                "house_id": int(org["house_id"]),
                "property_id": int(org["property_id"]),
                "tenant_id": int(org["tenant_id"]),
            },
        ).scalar_one()
    if revision != "20260919_0009" or int(users) != 4 or int(property_count) != 1 or int(house_count) != 1:
        raise RuntimeError("Fresh AUTH001 password fixture no longer matches the isolated database")


def _grant_property_admin(
    engine,
    *,
    user_id: int,
    tenant_id: int,
    property_id: int,
    request_id: str,
) -> None:
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        run_owned_transaction(
            session,
            lambda: RbacMutationService(session).grant_user_role_for_development_seed(
                target_user_id=user_id,
                role_code="PROPERTY_ADMIN",
                scope=OrganizationScope(
                    ScopeType.PROPERTY,
                    tenant_id=tenant_id,
                    property_id=property_id,
                ),
                request_id=request_id,
            ),
        )
    finally:
        session.close()


def _scope_has_permission(
    me: dict[str, object],
    *,
    scope_type: str,
    tenant_id: int,
    property_id: int | None,
) -> bool:
    for scope in me.get("scopes") or []:
        if not isinstance(scope, dict):
            continue
        if (
            scope.get("scope_type") == scope_type
            and scope.get("tenant_id") == tenant_id
            and scope.get("property_id") == property_id
            and "sla:manage" in (scope.get("permissions") or [])
        ):
            return True
    return False


def _policy(items: list[object], *, property_id: int | None) -> dict[str, object] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        if (
            item.get("category") == API_CATEGORY
            and item.get("priority") == API_PRIORITY
            and item.get("property_id") == property_id
        ):
            return item
    return None


def _put_body(
    *,
    tenant_id: int,
    property_id: int | None,
    respond: int,
    complete: int,
    expected_updated_at: str | None = None,
    status: str = "ACTIVE",
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "property_id": property_id,
        "category": API_CATEGORY,
        "priority": API_PRIORITY,
        "respond_minutes": respond,
        "complete_minutes": complete,
        "status": status,
        "expected_updated_at": expected_updated_at,
    }


def _concurrent_update(
    *,
    run_id: str,
    token: str,
    tenant_id: int,
    property_id: int,
    expected_updated_at: str,
) -> list[dict[str, object]]:
    barrier = threading.Barrier(2)
    candidates = ((16, 46), (18, 48))

    def send(index: int, respond: int, complete: int) -> dict[str, object]:
        request_id = f"{run_id}-concurrent-{index}"
        with httpx.Client(base_url=API, timeout=30.0) as concurrent_client:
            barrier.wait(timeout=10)
            response = concurrent_client.put(
                "/admin/sla-policies",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-Id": request_id,
                },
                json=_put_body(
                    tenant_id=tenant_id,
                    property_id=property_id,
                    respond=respond,
                    complete=complete,
                    expected_updated_at=expected_updated_at,
                ),
            )
            payload = response.json()
        return {
            "label": f"concurrent-{index}",
            "request_id": request_id,
            "status": response.status_code,
            "error_code": _error_code(payload),
            "requested": {"respond_minutes": respond, "complete_minutes": complete},
            "data": payload.get("data") if isinstance(payload.get("data"), dict) else None,
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(send, index, *candidate)
            for index, candidate in enumerate(candidates, start=1)
        ]
        return [future.result() for future in futures]


def _work_order_snapshot(engine, work_order_id: int) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT wo.id, wo.sla_policy_id, wo.created_at, wo.sla_due_at, "
                "sc.accept_due_at, sc.respond_due_at, sc.complete_due_at, "
                "sc.warning_at, sc.state "
                "FROM work_orders AS wo "
                "JOIN sla_clocks AS sc ON sc.work_order_id = wo.id "
                "WHERE wo.id = :work_order_id"
            ),
            {"work_order_id": work_order_id},
        ).mappings().one()
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def main() -> int:
    if PRIVATE_STATE.exists():
        raise RuntimeError(
            "A prior SLA private run state exists; inspect/resume it instead of creating duplicate objects"
        )
    settings = build_isolated_settings(enable_enqueue=True)
    _guard(settings)
    source = json.loads(SOURCE_CONTEXT.read_text(encoding="utf-8"))
    engine = create_database_engine(settings.active_database_url)
    run_id = f"sla-policy-api-{secrets.token_hex(5)}"
    property_admin_phone = _phone()
    state: dict[str, object] = {
        "task_id": TASK_ID,
        "run_id": run_id,
        "stage": "started",
        "phones": {"property_admin": property_admin_phone},
        "sessions": {},
        "request_ids": {},
        "org": source["org"],
        "api_policy": {"category": API_CATEGORY, "priority": API_PRIORITY},
        "page_policy": {"category": PAGE_CATEGORY, "priority": PAGE_PRIORITY},
    }
    _save_private(state)
    client = Client(run_id)
    try:
        _assert_source_fixture(engine, source)
        tenant_id = int(source["org"]["tenant_id"])
        property_id = int(source["org"]["property_id"])
        house_id = int(source["org"]["house_id"])

        sessions = {
            name: client.sms_login(str(source["phones"][name]), name)
            for name in ("primary", "secondary", "manager", "staff")
        }
        property_admin = client.sms_login(property_admin_phone, "property-admin-before-grant")
        property_admin_user_id = int(property_admin["user_id"])
        client.data(
            "PATCH",
            "/me/profile",
            token=str(property_admin["access_token"]),
            body={"display_name": f"SLA-property-admin-{run_id[-10:]}"},
            label="property-admin-profile",
        )
        fixture_grant_request_id = f"{run_id}-fixture-property-admin-grant"
        _grant_property_admin(
            engine,
            user_id=property_admin_user_id,
            tenant_id=tenant_id,
            property_id=property_id,
            request_id=fixture_grant_request_id,
        )
        property_admin = client.sms_login(property_admin_phone, "property-admin-after-grant")
        sessions["property_admin"] = property_admin
        state.update(
            {
                "stage": "authenticated",
                "sessions": sessions,
                "phones": {
                    **source["phones"],
                    "property_admin": property_admin_phone,
                },
                "fixture_grant_request_id": fixture_grant_request_id,
                "property_admin_user_id": property_admin_user_id,
            }
        )
        _save_private(state)
        tokens = {name: str(value["access_token"]) for name, value in sessions.items()}

        identities = {
            name: client.data("GET", "/me", token=tokens[name], label=f"{name}-me")
            for name in ("primary", "secondary", "manager", "staff", "property_admin")
        }
        if not _scope_has_permission(
            identities["manager"],
            scope_type="TENANT",
            tenant_id=tenant_id,
            property_id=None,
        ):
            raise RuntimeError("Fresh Tenant Admin /me did not include scoped sla:manage")
        if not _scope_has_permission(
            identities["property_admin"],
            scope_type="PROPERTY",
            tenant_id=tenant_id,
            property_id=property_id,
        ):
            raise RuntimeError("Fresh Property Admin /me did not include scoped sla:manage")
        for denied_name in ("primary", "secondary", "staff"):
            if "sla:manage" in (identities[denied_name].get("permissions") or []):
                raise RuntimeError(f"{denied_name} unexpectedly received sla:manage")

        baseline = client.data(
            "GET",
            f"/admin/sla-policies?property_id={property_id}&tenant_id={tenant_id}",
            token=tokens["manager"],
            label="tenant-admin-list-baseline",
        )
        if len(baseline.get("effective") or []) != 24:
            raise RuntimeError("SLA effective matrix did not contain 24 category/priority pairs")
        items = baseline.get("items") or []
        if _policy(items, property_id=None) or _policy(items, property_id=property_id):
            raise RuntimeError("The dedicated API evidence policy key is already occupied")
        if any(
            isinstance(item, dict)
            and item.get("category") == PAGE_CATEGORY
            and item.get("priority") == PAGE_PRIORITY
            for item in items
        ):
            raise RuntimeError("The dedicated page evidence policy key is already occupied")

        property_admin_list = client.data(
            "GET",
            f"/admin/sla-policies?property_id={property_id}&tenant_id={tenant_id}",
            token=tokens["property_admin"],
            label="property-admin-list",
        )
        denied_results: dict[str, dict[str, object]] = {}
        for denied_name in ("primary", "staff"):
            status, payload = client.call(
                "GET",
                f"/admin/sla-policies?property_id={property_id}&tenant_id={tenant_id}",
                token=tokens[denied_name],
                label=f"{denied_name}-list-denied",
                expected=(403,),
            )
            denied_results[denied_name] = {
                "status": status,
                "error_code": _error_code(payload),
            }
        mismatch_status, mismatch_payload = client.call(
            "GET",
            f"/admin/sla-policies?property_id={property_id}&tenant_id={tenant_id + 999999}",
            token=tokens["manager"],
            label="claimed-tenant-mismatch",
            expected=(403,),
        )

        invalid_results: dict[str, dict[str, object]] = {}
        invalid_requests = {
            "response-after-completion": _put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=31,
                complete=30,
            ),
            "tenant-disabled": _put_body(
                tenant_id=tenant_id,
                property_id=None,
                respond=17,
                complete=47,
                status="DISABLED",
            ),
            "boolean-minutes": {
                **_put_body(
                    tenant_id=tenant_id,
                    property_id=property_id,
                    respond=17,
                    complete=47,
                ),
                "respond_minutes": True,
            },
            "unexpected-field": {
                **_put_body(
                    tenant_id=tenant_id,
                    property_id=property_id,
                    respond=17,
                    complete=47,
                ),
                "unexpected": "rejected",
            },
        }
        for label, body in invalid_requests.items():
            status, payload = client.call(
                "PUT",
                "/admin/sla-policies",
                token=tokens["manager"],
                body=body,
                label=f"invalid-{label}",
                expected=(422,),
            )
            invalid_results[label] = {
                "status": status,
                "error_code": _error_code(payload),
            }
        denied_put_status, denied_put_payload = client.call(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=None,
                respond=17,
                complete=47,
            ),
            label="property-admin-tenant-default-denied",
            expected=(403,),
        )

        tenant_policy = client.data(
            "PUT",
            "/admin/sla-policies",
            token=tokens["manager"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=None,
                respond=17,
                complete=47,
            ),
            label="tenant-default-create",
        )
        property_policy = client.data(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=13,
                complete=43,
            ),
            label="property-override-create",
        )
        replay = client.data(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=13,
                complete=43,
            ),
            label="property-override-same-value-replay",
        )
        if int(replay["id"]) != int(property_policy["id"]):
            raise RuntimeError("Same-value replay returned a different policy")

        first_update = client.data(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=14,
                complete=44,
                expected_updated_at=str(property_policy["updated_at"]),
            ),
            label="property-override-first-update",
        )
        stale_status, stale_payload = client.call(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=15,
                complete=45,
                expected_updated_at=str(property_policy["updated_at"]),
            ),
            label="property-override-stale-update",
            expected=(409,),
        )
        concurrent = _concurrent_update(
            run_id=run_id,
            token=tokens["property_admin"],
            tenant_id=tenant_id,
            property_id=property_id,
            expected_updated_at=str(first_update["updated_at"]),
        )
        if sorted(int(item["status"]) for item in concurrent) != [200, 409]:
            raise RuntimeError(f"Concurrent update did not yield one winner and one conflict: {concurrent}")
        winner = next(item for item in concurrent if item["status"] == 200)
        loser = next(item for item in concurrent if item["status"] == 409)
        if loser["error_code"] != "SLA.CONFLICT":
            raise RuntimeError("Concurrent loser did not return SLA.CONFLICT")

        after_concurrency = client.data(
            "GET",
            f"/admin/sla-policies?property_id={property_id}&tenant_id={tenant_id}",
            token=tokens["property_admin"],
            label="list-after-concurrency",
        )
        current_policy = _policy(after_concurrency.get("items") or [], property_id=property_id)
        if current_policy is None or int(current_policy["id"]) != int(property_policy["id"]):
            raise RuntimeError("Property override disappeared after concurrent update")
        if {
            "respond_minutes": int(current_policy["respond_minutes"]),
            "complete_minutes": int(current_policy["complete_minutes"]),
        } != winner["requested"]:
            raise RuntimeError("Stored concurrent result does not match the successful writer")

        houses = client.data(
            "GET",
            "/me/houses",
            token=tokens["primary"],
            label="resident-houses",
        ).get("items") or []
        if not any(int(item["house_id"]) == house_id for item in houses):
            raise RuntimeError("Fresh resident no longer has the approved synthetic house relation")
        work_order = client.data(
            "POST",
            "/work-orders",
            token=tokens["primary"],
            headers={"Idempotency-Key": f"{run_id}-work-order"},
            body={
                "house_id": house_id,
                "text": f"{run_id} 合成电气插座冒火花验证 SLA 配置",
                "category": API_CATEGORY,
                "priority": API_PRIORITY,
                "severity": "HIGH",
                "tenant_id": tenant_id,
                "property_id": property_id,
            },
            label="resident-create-work-order",
        )
        work_order_id = int(work_order["id"])
        state.update(
            {
                "stage": "work_order_created",
                "request_ids": client.request_ids,
                "concurrent": concurrent,
                "tenant_policy": tenant_policy,
                "property_policy_before_work_order": current_policy,
                "work_order_id": work_order_id,
            }
        )
        _save_private(state)

        last_order = work_order
        for attempt in range(80):
            detail = client.data(
                "GET",
                f"/work-orders/{work_order_id}",
                token=tokens["primary"],
                label=f"work-order-poll-{attempt}",
            )
            candidate = detail.get("work_order")
            if isinstance(candidate, dict):
                last_order = candidate
                if candidate.get("status") != "PENDING_ANALYSIS":
                    break
            time.sleep(0.25)
        before_policy_change = _work_order_snapshot(engine, work_order_id)
        if int(before_policy_change["sla_policy_id"]) != int(current_policy["id"]):
            raise RuntimeError("New work order did not bind the active property SLA policy")

        post_order_policy = client.data(
            "PUT",
            "/admin/sla-policies",
            token=tokens["property_admin"],
            body=_put_body(
                tenant_id=tenant_id,
                property_id=property_id,
                respond=int(current_policy["respond_minutes"]) + 5,
                complete=int(current_policy["complete_minutes"]) + 5,
                expected_updated_at=str(current_policy["updated_at"]),
            ),
            label="policy-change-after-work-order",
        )
        after_policy_change = _work_order_snapshot(engine, work_order_id)
        if before_policy_change["sla_due_at"] != after_policy_change["sla_due_at"]:
            raise RuntimeError("Existing work-order completion deadline changed retroactively")
        if before_policy_change["complete_due_at"] != after_policy_change["complete_due_at"]:
            raise RuntimeError("Existing SLA complete clock changed retroactively")

        state.update(
            {
                "stage": "complete",
                "request_ids": client.request_ids,
                "concurrent": concurrent,
                "tenant_policy": tenant_policy,
                "property_policy_before_work_order": current_policy,
                "property_policy_final": post_order_policy,
                "work_order_id": work_order_id,
                "work_order_before_policy_change": before_policy_change,
                "work_order_after_policy_change": after_policy_change,
            }
        )
        _save_private(state)

        evidence = {
            "task_id": TASK_ID,
            "phase": "api_runtime",
            "captured_at": datetime.now(UTC).isoformat(),
            "code_sha": "7814d45531a91efb6a63c63df261415fa446a1c4",
            "run_id": run_id,
            "environment": {
                "api": API,
                "database": "127.0.0.1:3307/property_ai_auth001_test",
                "revision": "20260919_0009",
                "rabbit_vhost": ISOLATED_VHOST,
                "rabbit_queue": ISOLATED_QUEUE,
                "provider": "DEVELOPMENT_FAKE_AI",
            },
            "objects": {
                "tenant_id": tenant_id,
                "property_id": property_id,
                "house_id": house_id,
                "tenant_policy_id": int(tenant_policy["id"]),
                "property_policy_id": int(post_order_policy["id"]),
                "work_order_id": work_order_id,
                "property_admin_user_id": property_admin_user_id,
            },
            "checks": {
                "normal_development_sms_sessions": True,
                "tenant_admin_me_scoped_sla_manage": True,
                "property_admin_me_scoped_sla_manage": True,
                "resident_staff_permission_absent": True,
                "effective_matrix_entries": len(baseline["effective"]),
                "property_admin_list_entries": len(property_admin_list["effective"]),
                "resident_and_staff_denied": denied_results,
                "claimed_tenant_mismatch": {
                    "status": mismatch_status,
                    "error_code": _error_code(mismatch_payload),
                },
                "property_admin_tenant_default_denied": {
                    "status": denied_put_status,
                    "error_code": _error_code(denied_put_payload),
                },
                "invalid_requests": invalid_results,
                "same_value_replay_same_id": True,
                "stale_update": {
                    "status": stale_status,
                    "error_code": _error_code(stale_payload),
                },
                "concurrent_statuses": sorted(item["status"] for item in concurrent),
                "concurrent_winner": winner["requested"],
                "new_work_order_bound_property_policy": True,
                "existing_completion_deadline_unchanged_after_policy_update": True,
                "work_order_runtime_status": last_order.get("status"),
            },
            "request_id_hashes": {
                label: _hash(request_id)
                for label, request_id in client.request_ids.items()
            }
            | {
                item["label"]: _hash(str(item["request_id"]))
                for item in concurrent
            },
            "boundaries": {
                "seed_login_used": False,
                "dev_auth_used": False,
                "development_sms_test_inbox_used": True,
                "fixture_only_change": "audited Property Admin grant for a fresh normal SMS identity",
                "direct_sql_business_write": False,
                "migration_run": False,
                "shared_vhost_read_consumed_replayed_or_cleared": False,
                "real_model_claimed": False,
                "ui_claimed": False,
            },
            "private_context": "tmp/sla-policy-management-010/context.json",
            "redaction": "No phone, OTP, token, password, resident text, or full request ID is emitted.",
        }
        PUBLIC_EVIDENCE.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        state["request_ids"] = client.request_ids
        _save_private(state)
        client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
