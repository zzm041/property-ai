"""Scoped runtime evidence for KNOWLEDGE-MGMT-RAG-008.

Uses normal password auth and business APIs against the already-approved AUTH001
runtime. It never clears tables, runs migrations, or writes business state with SQL.
Tokens and account credentials stay in memory and are not included in the evidence.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

import httpx
from pymilvus import Collection, connections
from sqlalchemy import create_engine, text


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
ACCOUNTS_PATH = REPO / "reports" / "mp-current-full-regression" / "accounts.local.json"
EVIDENCE_PATH = Path(__file__).with_name("runtime-negative-concurrency-evidence.json")
BASE_URL = "http://127.0.0.1:8083/api/v1"
RUN_TAG = f"kb008-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(OPS))

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    apply_isolated_environ,
    build_isolated_settings,
)


def payload(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


def error_code(response: httpx.Response) -> str | None:
    body = payload(response)
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    request_id: str,
) -> httpx.Response:
    headers = {"X-Request-Id": request_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.request(
        method,
        f"{BASE_URL}{path}",
        headers=headers,
        json=body,
        timeout=15.0,
    )


def require_status(response: httpx.Response, expected: int, label: str) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(
            f"{label}: expected {expected}, got {response.status_code} {error_code(response)}"
        )
    return payload(response)


_LOGIN_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def login(accounts: dict[str, Any], label: str) -> tuple[str, dict[str, Any]]:
    """Authenticate through the normal SMS flow using the test-only SMS provider."""
    if label in _LOGIN_CACHE:
        return _LOGIN_CACHE[label]
    phone = accounts["phones"][label]
    challenge = require_status(
        request(
            "POST",
            "/auth/challenge",
            body={"phone": phone, "purpose": "login"},
            request_id=f"{RUN_TAG}-challenge-{label}",
        ),
        200,
        f"challenge {label}",
    )["data"]
    match = re.fullmatch(r"(\d+) \+ (\d+) = \?", challenge["prompt"].strip())
    if match is None:
        raise RuntimeError(f"unexpected challenge prompt for {label}")
    require_status(
        request(
            "POST",
            "/auth/sms/send",
            body={
                "phone": phone,
                "purpose": "login",
                "challenge_id": challenge["challenge_id"],
                "challenge_answer": str(int(match.group(1)) + int(match.group(2))),
            },
            request_id=f"{RUN_TAG}-sms-send-{label}",
        ),
        200,
        f"sms send {label}",
    )
    inbox = require_status(
        request(
            "POST",
            "/auth/sms/test-inbox",
            body={"phone": phone, "purpose": "login"},
            request_id=f"{RUN_TAG}-sms-inbox-{label}",
        ),
        200,
        f"sms inbox {label}",
    )["data"]
    response = request(
        "POST",
        "/auth/sms/login",
        body={"phone": phone, "code": inbox["code"]},
        request_id=f"{RUN_TAG}-login-{label}",
    )
    data = require_status(response, 200, f"login {label}")["data"]
    token = data["access_token"]
    me = require_status(
        request("GET", "/me", token=token, request_id=f"{RUN_TAG}-me-{label}"),
        200,
        f"me {label}",
    )["data"]
    _LOGIN_CACHE[label] = (token, me)
    return _LOGIN_CACHE[label]


def wait_document(
    token: str,
    document_id: int,
    *,
    versions: set[int],
    terminal: set[str] = frozenset({"SUCCEEDED", "FAILED"}),
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = request(
            "GET",
            f"/admin/knowledge-documents/{document_id}",
            token=token,
            request_id=f"{RUN_TAG}-poll-{document_id}-{uuid4().hex[:6]}",
        )
        last = require_status(response, 200, f"poll document {document_id}")["data"]
        jobs = {int(item["version"]): item for item in last["ingestion_jobs"]}
        if all(version in jobs and jobs[version]["status"] in terminal for version in versions):
            return last
        time.sleep(0.5)
    raise RuntimeError(f"document {document_id} did not reach terminal state: {last}")


def create_document(
    token: str,
    *,
    tenant_id: int,
    property_id: int,
    title: str,
    visibility: str,
    source_text: str,
) -> tuple[int, int]:
    response = request(
        "POST",
        "/admin/knowledge-documents",
        token=token,
        body={
            "tenant_id": tenant_id,
            "property_id": property_id,
            "title": title,
            "document_type": "FAQ",
            "visibility": visibility,
            "source_text": source_text,
        },
        request_id=f"{RUN_TAG}-create-{uuid4().hex[:6]}",
    )
    data = require_status(response, 200, f"create {title}")["data"]
    return int(data["document_id"]), int(data["version"])


def archive_document(
    token: str,
    *,
    document_id: int,
    tenant_id: int,
    property_id: int,
) -> dict[str, Any]:
    response = request(
        "POST",
        f"/admin/knowledge-documents/{document_id}/archive",
        token=token,
        body={"tenant_id": tenant_id, "property_id": property_id},
        request_id=f"{RUN_TAG}-archive-{document_id}",
    )
    return require_status(response, 200, f"archive {document_id}")["data"]


def query(token: str, *, question: str, property_id: int, tenant_id: int | None = None) -> httpx.Response:
    body: dict[str, Any] = {"question": question, "property_id": property_id}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    return request(
        "POST",
        "/assistant/query",
        token=token,
        body=body,
        request_id=f"{RUN_TAG}-query-{uuid4().hex[:6]}",
    )


def main() -> None:
    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    manager_token, manager_me = login(accounts, "property_admin")
    resident_token, resident_me = login(accounts, "resident")
    tenant_token, tenant_me = login(accounts, "tenant_admin")

    manager_scope = next(
        scope
        for scope in manager_me["scopes"]
        if scope["scope_type"] == "PROPERTY" and "knowledge:manage" in scope["permissions"]
    )
    tenant_id = int(manager_scope["tenant_id"])
    property_id = int(manager_scope["property_id"])

    listed = require_status(
        request(
            "GET",
            f"/admin/knowledge-documents?property_id={property_id}&include_archived=true&limit=100",
            token=manager_token,
            request_id=f"{RUN_TAG}-list",
        ),
        200,
        "list knowledge",
    )["data"]["items"]
    main_document = next(
        item for item in listed if item["title"].startswith("KB008-20260920-1933")
    )
    main_document_id = int(main_document["id"])

    unauthorized_create = request(
        "POST",
        "/admin/knowledge-documents",
        token=resident_token,
        body={
            "tenant_id": tenant_id,
            "property_id": property_id,
            "title": f"{RUN_TAG}-forbidden",
            "document_type": "FAQ",
            "visibility": "RESIDENT",
            "source_text": "must not be created",
        },
        request_id=f"{RUN_TAG}-forbidden-create",
    )
    unauthorized_view = request(
        "GET",
        f"/admin/knowledge-documents/{main_document_id}",
        token=resident_token,
        request_id=f"{RUN_TAG}-forbidden-view",
    )
    unauthorized_archive = request(
        "POST",
        f"/admin/knowledge-documents/{main_document_id}/archive",
        token=resident_token,
        body={"tenant_id": tenant_id, "property_id": property_id},
        request_id=f"{RUN_TAG}-forbidden-archive",
    )
    for label, response in (
        ("create", unauthorized_create),
        ("view", unauthorized_view),
        ("archive", unauthorized_archive),
    ):
        if response.status_code not in {403, 404}:
            raise RuntimeError(f"resident {label} unexpectedly returned {response.status_code}")

    tenant_mismatch = query(
        resident_token,
        question="tenant mismatch probe",
        property_id=property_id,
        tenant_id=tenant_id + 999_999,
    )
    missing_property = query(
        resident_token,
        question="property mismatch probe",
        property_id=2_147_483_647,
    )
    if tenant_mismatch.status_code != 422 or missing_property.status_code != 404:
        raise RuntimeError(
            "cross-scope probes failed: "
            f"tenant={tenant_mismatch.status_code}/{error_code(tenant_mismatch)} "
            f"property={missing_property.status_code}/{error_code(missing_property)}"
        )

    internal_marker = f"{RUN_TAG}-STAFF-ONLY"
    internal_id, internal_version = create_document(
        manager_token,
        tenant_id=tenant_id,
        property_id=property_id,
        title=f"{RUN_TAG}-internal",
        visibility="STAFF_ONLY",
        source_text=f"Internal synthetic maintenance marker {internal_marker}.",
    )
    internal_detail = wait_document(
        manager_token,
        internal_id,
        versions={internal_version},
    )
    resident_internal = require_status(
        query(resident_token, question=internal_marker, property_id=property_id),
        200,
        "resident internal query",
    )["data"]
    manager_internal = require_status(
        query(manager_token, question=internal_marker, property_id=property_id),
        200,
        "manager internal query",
    )["data"]
    resident_internal_ids = {int(item["document_id"]) for item in resident_internal["citations"]}
    manager_internal_ids = {int(item["document_id"]) for item in manager_internal["citations"]}
    if internal_id in resident_internal_ids or internal_id not in manager_internal_ids:
        raise RuntimeError("STAFF_ONLY visibility runtime check failed")

    concurrent_marker = f"{RUN_TAG}-CONCURRENT"
    concurrent_id, first_version = create_document(
        manager_token,
        tenant_id=tenant_id,
        property_id=property_id,
        title=f"{RUN_TAG}-concurrent",
        visibility="RESIDENT",
        source_text=f"{concurrent_marker} initial version.",
    )
    wait_document(manager_token, concurrent_id, versions={first_version})

    def post_version(suffix: str) -> tuple[int, int, str | None]:
        response = request(
            "POST",
            f"/admin/knowledge-documents/{concurrent_id}/versions",
            token=manager_token,
            body={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "source_text": f"{concurrent_marker} concurrent version {suffix}.",
            },
            request_id=f"{RUN_TAG}-version-{suffix}",
        )
        body = payload(response)
        version = int(body.get("data", {}).get("version", 0))
        return response.status_code, version, error_code(response)

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent_results = list(pool.map(post_version, ("A", "B")))
    if {item[0] for item in concurrent_results} != {200}:
        raise RuntimeError(f"concurrent version requests failed: {concurrent_results}")
    allocated_versions = {item[1] for item in concurrent_results}
    if len(allocated_versions) != 2 or min(allocated_versions) <= first_version:
        raise RuntimeError(f"concurrent versions were not unique: {concurrent_results}")
    concurrent_detail = wait_document(
        manager_token,
        concurrent_id,
        versions=allocated_versions,
    )
    latest_version = max(allocated_versions)

    settings = build_isolated_settings()
    apply_isolated_environ(settings)
    from app.worker.tasks import ingest_knowledge_task

    before_attempt = next(
        int(item["attempt"])
        for item in concurrent_detail["ingestion_jobs"]
        if int(item["version"]) == latest_version
    )
    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        mysql_chunks_before = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE document_id = :document_id AND version = :version"
                ),
                {"document_id": concurrent_id, "version": latest_version},
            ).scalar_one()
        )
    duplicate_tasks = [
        str(
            ingest_knowledge_task.apply_async(
                args=(concurrent_id, latest_version, f"{RUN_TAG}-repeat-{index}"),
                queue=ISOLATED_QUEUE,
            ).id
        )
        for index in (1, 2)
    ]
    time.sleep(4)
    repeated_detail = wait_document(
        manager_token,
        concurrent_id,
        versions={latest_version},
    )
    after_attempt = next(
        int(item["attempt"])
        for item in repeated_detail["ingestion_jobs"]
        if int(item["version"]) == latest_version
    )
    with engine.connect() as connection:
        mysql_chunks_after = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_chunks "
                    "WHERE document_id = :document_id AND version = :version"
                ),
                {"document_id": concurrent_id, "version": latest_version},
            ).scalar_one()
        )
        audit_rows = connection.execute(
            text(
                "SELECT action, resource_type, resource_id, request_id "
                "FROM audit_logs WHERE resource_type = 'KNOWLEDGE_DOCUMENT' "
                "AND resource_id IN (:main_id, :internal_id, :concurrent_id) "
                "ORDER BY id"
            ),
            {
                "main_id": main_document_id,
                "internal_id": internal_id,
                "concurrent_id": concurrent_id,
            },
        ).mappings().all()
    if before_attempt != after_attempt or mysql_chunks_before != mysql_chunks_after:
        raise RuntimeError(
            "duplicate processing changed a completed version: "
            f"attempt {before_attempt}->{after_attempt}, "
            f"chunks {mysql_chunks_before}->{mysql_chunks_after}"
        )

    alias = f"{RUN_TAG}-probe"
    connections.connect(alias=alias, uri=settings.milvus_uri, timeout=3)
    collection = Collection(settings.milvus_collection, using=alias)
    collection.load()
    milvus_rows = collection.query(
        expr=f"document_id == {concurrent_id} and document_version == {latest_version}",
        output_fields=["chunk_id", "document_id", "document_version", "tenant_id", "property_id", "visibility"],
        timeout=3,
    )
    milvus_schema = [
        {"name": field.name, "dtype": str(field.dtype), "params": dict(field.params)}
        for field in collection.schema.fields
    ]
    connections.disconnect(alias)

    archived_internal = archive_document(
        manager_token,
        document_id=internal_id,
        tenant_id=tenant_id,
        property_id=property_id,
    )
    archived_concurrent = archive_document(
        manager_token,
        document_id=concurrent_id,
        tenant_id=tenant_id,
        property_id=property_id,
    )
    archived_query = require_status(
        query(resident_token, question=concurrent_marker, property_id=property_id),
        200,
        "archived auxiliary query",
    )["data"]
    if concurrent_id in {int(item["document_id"]) for item in archived_query["citations"]}:
        raise RuntimeError("archived auxiliary document remained retrievable")

    mixed_candidates = []
    for label in ("tenant_admin", "property_admin", "resident", "staff"):
        try:
            _, me = login(accounts, label)
        except Exception:
            continue
        tenant_ids = sorted(
            {int(scope["tenant_id"]) for scope in me["scopes"] if scope.get("tenant_id")}
        )
        property_ids = sorted(
            {int(scope["property_id"]) for scope in me["scopes"] if scope.get("property_id")}
        )
        if len(me["scopes"]) > 1:
            mixed_candidates.append(
                {
                    "account_label": label,
                    "roles": sorted(me["roles"]),
                    "tenant_ids": tenant_ids,
                    "property_ids": property_ids,
                }
            )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence = {
        "task_id": "KNOWLEDGE-MGMT-RAG-008",
        "run_tag": RUN_TAG,
        "recorded_at": datetime.now(UTC).isoformat(),
        "code_sha": head,
        "runtime": {
            "api": BASE_URL,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "alembic": "20260919_0009",
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
            "milvus_collection": settings.milvus_collection,
            "embedding_provider": settings.embedding_provider,
            "embedding_dimensions": settings.embedding_dimensions,
        },
        "identities": {
            "manager": {
                "user_id": manager_me["user_id"],
                "roles": manager_me["roles"],
                "scope_types": [item["scope_type"] for item in manager_me["scopes"]],
            },
            "resident": {
                "user_id": resident_me["user_id"],
                "roles": resident_me["roles"],
                "scope_types": [item["scope_type"] for item in resident_me["scopes"]],
            },
            "tenant_admin": {
                "user_id": tenant_me["user_id"],
                "roles": tenant_me["roles"],
                "scope_types": [item["scope_type"] for item in tenant_me["scopes"]],
            },
            "credentials_recorded": False,
        },
        "main_document": {
            "document_id": main_document_id,
            "title": main_document["title"],
            "status_before_page_archive": main_document["status"],
            "current_version": main_document["current_version"],
        },
        "authorization_negatives": {
            "resident_create": {
                "status": unauthorized_create.status_code,
                "error_code": error_code(unauthorized_create),
            },
            "resident_view": {
                "status": unauthorized_view.status_code,
                "error_code": error_code(unauthorized_view),
            },
            "resident_archive": {
                "status": unauthorized_archive.status_code,
                "error_code": error_code(unauthorized_archive),
            },
            "claimed_tenant_mismatch": {
                "status": tenant_mismatch.status_code,
                "error_code": error_code(tenant_mismatch),
            },
            "missing_property": {
                "status": missing_property.status_code,
                "error_code": error_code(missing_property),
            },
            "mixed_role_runtime_candidates": mixed_candidates,
            "mixed_role_boundary": (
                "Runtime candidate details recorded when present; focused unit coverage remains the "
                "evidence for the two-tenant mixed-grant branch if this isolated dataset has none."
            ),
        },
        "staff_only": {
            "document_id": internal_id,
            "version": internal_version,
            "job_status": next(
                item["status"]
                for item in internal_detail["ingestion_jobs"]
                if int(item["version"]) == internal_version
            ),
            "resident_citation_ids": sorted(resident_internal_ids),
            "manager_citation_ids": sorted(manager_internal_ids),
            "archived_status": archived_internal["status"],
        },
        "concurrency_and_repeat": {
            "document_id": concurrent_id,
            "first_version": first_version,
            "concurrent_results": [
                {"status": status, "version": version, "error_code": code}
                for status, version, code in concurrent_results
            ],
            "allocated_versions": sorted(allocated_versions),
            "current_version": repeated_detail["current_version"],
            "duplicate_task_ids": duplicate_tasks,
            "attempt_before_after": [before_attempt, after_attempt],
            "mysql_chunks_before_after": [mysql_chunks_before, mysql_chunks_after],
            "milvus_rows": milvus_rows,
            "archived_status": archived_concurrent["status"],
            "archived_retrieval_citation_ids": sorted(
                int(item["document_id"]) for item in archived_query["citations"]
            ),
        },
        "milvus_schema": milvus_schema,
        "audit_rows": [dict(row) for row in audit_rows],
        "boundary": {
            "page_positive_flow": "separate DevTools evidence",
            "api_negatives": True,
            "direct_database_business_writes": False,
            "migrations": False,
            "model_mode": "DEVELOPMENT_FAKE",
            "real_bge_m3_qwen": "NOT_RUN",
        },
    }
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "evidence": str(EVIDENCE_PATH),
                "main_document_id": main_document_id,
                "staff_only_document_id": internal_id,
                "concurrent_document_id": concurrent_id,
                "resident_management_statuses": [
                    unauthorized_create.status_code,
                    unauthorized_view.status_code,
                    unauthorized_archive.status_code,
                ],
                "allocated_versions": sorted(allocated_versions),
                "repeat_attempt_unchanged": before_attempt == after_attempt,
                "repeat_chunks_unchanged": mysql_chunks_before == mysql_chunks_after,
                "real_models": "NOT_RUN",
            },
            ensure_ascii=False,
        )
    )
    engine.dispose()


if __name__ == "__main__":
    main()
