"""Focused AUTH001 runtime verification for ANN007 scope and paging fixes."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
for candidate in (BACKEND, OPS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.domain.identity import mask_phone  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402
from verify_announcement_governance_007 import _expect_error  # noqa: E402
from verify_property_house_relation_006 import _request, _sms_login  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _manager_pair(connection) -> dict[str, object]:
    row = connection.execute(
        text(
            "SELECT ta.user_id AS tenant_user_id, tp.phone_e164 AS tenant_phone, "
            "ta.tenant_id, pa.user_id AS property_user_id, "
            "pp.phone_e164 AS property_phone, pa.property_id "
            "FROM user_roles ta "
            "JOIN roles tr ON tr.id = ta.role_id AND BINARY tr.code = 'TENANT_ADMIN' "
            "JOIN users tu ON tu.id = ta.user_id AND BINARY tu.status = 'ACTIVE' "
            "JOIN user_phones tp ON tp.user_id = tu.id AND BINARY tp.status = 'ACTIVE' "
            "JOIN user_roles pa ON pa.tenant_id = ta.tenant_id "
            " AND BINARY pa.scope_type = 'PROPERTY' "
            "JOIN roles pr ON pr.id = pa.role_id AND BINARY pr.code = 'PROPERTY_ADMIN' "
            "JOIN users pu ON pu.id = pa.user_id AND BINARY pu.status = 'ACTIVE' "
            "JOIN user_phones pp ON pp.user_id = pu.id AND BINARY pp.status = 'ACTIVE' "
            "WHERE BINARY ta.scope_type = 'TENANT' "
            " AND NOT EXISTS ("
            "   SELECT 1 FROM user_roles broader "
            "   JOIN role_permissions rp ON rp.role_id = broader.role_id "
            "   JOIN permissions p ON p.id = rp.permission_id "
            "   WHERE broader.user_id = pa.user_id "
            "    AND broader.tenant_id = pa.tenant_id "
            "    AND BINARY broader.scope_type = 'TENANT' "
            "    AND BINARY p.code IN ('announcement:draft', 'announcement:publish')"
            " ) "
            "ORDER BY ta.id, pa.id LIMIT 1"
        )
    ).mappings().first()
    if row is None:
        raise RuntimeError(
            "No tenant-admin/property-admin pair with distinct effective scopes is available"
        )
    return dict(row)


def _announcement_scopes(me: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for scope in me.get("scopes", []):
        permissions = [
            item
            for item in scope.get("permissions", [])
            if item.startswith("announcement:")
        ]
        if permissions:
            result.append(
                {
                    "role_code": scope["role_code"],
                    "scope_type": scope["scope_type"],
                    "tenant_id": scope.get("tenant_id"),
                    "property_id": scope.get("property_id"),
                    "permissions": permissions,
                }
            )
    return result


def _all_managed(
    client: httpx.Client,
    *,
    token: str,
    property_id: int,
    request_prefix: str,
) -> tuple[list[dict[str, object]], list[int]]:
    items: list[dict[str, object]] = []
    cursors: list[int] = []
    after_id = 0
    while True:
        payload = _request(
            client,
            "GET",
            f"/admin/announcements?property_id={property_id}&limit=2&after_id={after_id}",
            token=token,
            request_id=f"{request_prefix}-page-{len(cursors) + 1}",
        )
        page = payload.get("items", [])
        items.extend(page)
        cursor = payload.get("next_cursor")
        if cursor is None:
            break
        cursor = int(cursor)
        if cursor <= after_id:
            raise RuntimeError("Management pagination cursor did not advance")
        cursors.append(cursor)
        after_id = cursor
        if len(cursors) > 100:
            raise RuntimeError("Management pagination exceeded the bounded verification limit")
    ids = [int(item["id"]) for item in items]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Management pagination returned duplicate announcement ids")
    return items, cursors


def run() -> dict[str, object]:
    run_id = uuid4().hex[:10]
    request_prefix = f"ann007-fix1-{run_id}"
    settings = build_isolated_settings(enable_enqueue=True)
    target = make_url(settings.test_database_url)
    if (
        target.host != "127.0.0.1"
        or target.port != 3307
        or target.database != "property_ai_auth001_test"
    ):
        raise RuntimeError("AUTH001 target guard rejected the database")

    engine = create_engine(settings.test_database_url)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "20260919_0009":
            raise RuntimeError(f"Expected 0009, found {revision}")
        pair = _manager_pair(connection)

    property_id = int(pair["property_id"])
    with httpx.Client(timeout=20.0) as client:
        health = client.get(f"{API}/healthz")
        if health.status_code != 200:
            raise RuntimeError(f"8083 health returned {health.status_code}")
        tenant_session = _sms_login(
            client,
            phone=str(pair["tenant_phone"]),
            request_prefix=f"{request_prefix}-tenant",
        )
        property_session = _sms_login(
            client,
            phone=str(pair["property_phone"]),
            request_prefix=f"{request_prefix}-property",
        )
        tenant_token = str(tenant_session["access_token"])
        property_token = str(property_session["access_token"])
        tenant_me = _request(
            client,
            "GET",
            "/me",
            token=tenant_token,
            request_id=f"{request_prefix}-tenant-me",
        )
        property_me = _request(
            client,
            "GET",
            "/me",
            token=property_token,
            request_id=f"{request_prefix}-property-me",
        )

        draft = _request(
            client,
            "POST",
            "/admin/announcements",
            token=tenant_token,
            request_id=f"{request_prefix}-draft",
            json_body={
                "property_id": property_id,
                "title": f"ANN007 范围守卫 {run_id}",
                "content": "租户级公告只能由覆盖该租户的授权管理员提交和审核。",
                "scope_type": "TENANT",
                "audience": "RESIDENT",
            },
        )
        announcement_id = int(draft["id"])
        _expect_error(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/submit-review",
            token=property_token,
            request_id=f"{request_prefix}-property-submit-denied",
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
            json_body={},
        )
        _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/submit-review",
            token=tenant_token,
            request_id=f"{request_prefix}-tenant-submit",
            json_body={},
        )
        _expect_error(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=property_token,
            request_id=f"{request_prefix}-property-publish-denied",
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
            json_body={"decision": "APPROVE"},
        )
        published = _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=tenant_token,
            request_id=f"{request_prefix}-tenant-publish",
            json_body={"decision": "APPROVE"},
        )
        if published["status"] != "PUBLISHED":
            raise RuntimeError("Tenant-authorized approval did not publish the announcement")

        _expect_error(
            client,
            "GET",
            f"/admin/announcements/{announcement_id}",
            token=property_token,
            request_id=f"{request_prefix}-property-detail-denied",
            expected_status=403,
            expected_code="IAM.FORBIDDEN",
            json_body={},
        )
        tenant_items, tenant_cursors = _all_managed(
            client,
            token=tenant_token,
            property_id=property_id,
            request_prefix=f"{request_prefix}-tenant-list",
        )
        property_items, property_cursors = _all_managed(
            client,
            token=property_token,
            property_id=property_id,
            request_prefix=f"{request_prefix}-property-list",
        )
        tenant_ids = [int(item["id"]) for item in tenant_items]
        property_ids = [int(item["id"]) for item in property_items]
        if tenant_ids.count(announcement_id) != 1:
            raise RuntimeError("Tenant management pagination did not return the target exactly once")
        if announcement_id in property_ids:
            raise RuntimeError("Property management pagination exposed a tenant-target announcement")
        if not tenant_cursors or not property_cursors:
            raise RuntimeError("Existing data did not exercise multiple management pages")

    with engine.connect() as connection:
        persisted = connection.execute(
            text(
                "SELECT a.id, a.status, s.scope_type, a.published_by_user_id "
                "FROM announcements a "
                "JOIN announcement_scopes s ON s.announcement_id = a.id "
                "WHERE a.id = :id"
            ),
            {"id": announcement_id},
        ).mappings().one()
        audit_actions = connection.execute(
            text(
                "SELECT action FROM audit_logs "
                "WHERE resource_type = 'ANNOUNCEMENT' AND resource_id = :id "
                "ORDER BY id"
            ),
            {"id": announcement_id},
        ).scalars().all()

    return {
        "task_id": "ANNOUNCEMENT-GOVERNANCE-007-FIX-1",
        "run_id": run_id,
        "code_sha": "90752f3",
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "provider_boundary": "DEVELOPMENT SMS; no AI used",
        },
        "tenant_manager": {
            "user_id": pair["tenant_user_id"],
            "phone_masked": mask_phone(str(pair["tenant_phone"])),
            "official_sms_session": tenant_session["auth_method"] == "sms",
            "announcement_scopes": _announcement_scopes(tenant_me),
        },
        "property_manager": {
            "user_id": pair["property_user_id"],
            "phone_masked": mask_phone(str(pair["property_phone"])),
            "official_sms_session": property_session["auth_method"] == "sms",
            "announcement_scopes": _announcement_scopes(property_me),
        },
        "announcement": {
            "id": announcement_id,
            "property_id": property_id,
            "persisted": dict(persisted),
            "audit_actions": list(audit_actions),
        },
        "management_pagination": {
            "limit": 2,
            "tenant_item_count": len(tenant_items),
            "tenant_cursors": tenant_cursors,
            "property_item_count": len(property_items),
            "property_cursors": property_cursors,
            "no_duplicates": True,
        },
        "checks": {
            "property_submit_tenant_target_denied": True,
            "property_publish_tenant_target_denied": True,
            "property_detail_tenant_target_denied": True,
            "tenant_submit_and_publish_succeeded": True,
            "property_list_excludes_tenant_target": True,
            "tenant_list_contains_target_once": True,
            "multiple_pages_verified": True,
            "business_state_writes_via_api_only": True,
            "database_evidence_queries_read_only": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=_default)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
