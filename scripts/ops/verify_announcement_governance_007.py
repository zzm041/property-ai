"""Non-destructive AUTH001 verification for ANNOUNCEMENT-GOVERNANCE-007."""

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

from app.domain.phone import mask_phone  # noqa: E402
from auth001_isolated_runtime import build_isolated_settings  # noqa: E402
from verify_property_house_relation_006 import _request, _sms_login  # noqa: E402

API = "http://127.0.0.1:8083/api/v1"


def _default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _manager_and_targets(connection) -> tuple[dict[str, object], list[dict[str, object]]]:
    managers = connection.execute(
        text(
            "SELECT u.id AS user_id, up.phone_e164, ur.tenant_id, "
            "r.code AS role_code "
            "FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN users u ON u.id = ur.user_id AND BINARY u.status = 'ACTIVE' "
            "JOIN user_phones up ON up.user_id = u.id AND BINARY up.status = 'ACTIVE' "
            "WHERE r.code IN ('TENANT_ADMIN', 'PROPERTY_ADMIN') "
            "ORDER BY CASE r.code WHEN 'TENANT_ADMIN' THEN 0 ELSE 1 END, ur.id"
        )
    ).mappings().all()
    for raw in managers:
        manager = dict(raw)
        relations = connection.execute(
            text(
                "SELECT rr.user_id, up.phone_e164, rr.property_id, "
                "rr.relation_type, h.id AS house_id, h.unit_id, u.building_id "
                "FROM resident_house_relations rr "
                "JOIN users usr ON usr.id = rr.user_id AND BINARY usr.status = 'ACTIVE' "
                "JOIN user_phones up ON up.user_id = usr.id AND BINARY up.status = 'ACTIVE' "
                "JOIN houses h ON h.id = rr.house_id "
                " AND h.tenant_id = rr.tenant_id AND h.property_id = rr.property_id "
                "JOIN units u ON u.id = h.unit_id "
                " AND u.tenant_id = h.tenant_id AND u.property_id = h.property_id "
                "WHERE rr.tenant_id = :tenant_id AND BINARY rr.status = 'ACTIVE' "
                " AND rr.valid_from <= UTC_TIMESTAMP(6) "
                " AND (rr.valid_to IS NULL OR rr.valid_to > UTC_TIMESTAMP(6)) "
                "ORDER BY rr.property_id, rr.id"
            ),
            {"tenant_id": manager["tenant_id"]},
        ).mappings().all()
        if relations:
            return manager, [dict(item) for item in relations]
    raise RuntimeError("No official-login manager with active resident targets is available")


def _staff_target(connection, *, tenant_id: int, property_id: int) -> dict[str, object] | None:
    row = connection.execute(
        text(
            "SELECT e.user_id, up.phone_e164 "
            "FROM employees e "
            "JOIN employee_properties ep ON ep.employee_id = e.id "
            " AND ep.tenant_id = e.tenant_id "
            "JOIN users u ON u.id = e.user_id AND BINARY u.status = 'ACTIVE' "
            "JOIN user_phones up ON up.user_id = u.id AND BINARY up.status = 'ACTIVE' "
            "WHERE e.tenant_id = :tenant_id AND ep.property_id = :property_id "
            " AND BINARY e.status = 'ACTIVE' AND BINARY ep.status = 'ACTIVE' "
            " AND ep.valid_from <= UTC_TIMESTAMP(6) "
            " AND (ep.valid_to IS NULL OR ep.valid_to > UTC_TIMESTAMP(6)) "
            "ORDER BY e.id LIMIT 1"
        ),
        {"tenant_id": tenant_id, "property_id": property_id},
    ).mappings().first()
    return dict(row) if row else None


def _expect_error(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str,
    request_id: str,
    expected_status: int,
    expected_code: str,
    json_body: dict[str, object],
) -> dict[str, object]:
    response = client.request(
        method,
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-Id": request_id,
        },
        json=json_body,
    )
    payload = response.json()
    error = payload.get("error", {})
    if response.status_code != expected_status or error.get("code") != expected_code:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}/{error.get('code')}, "
            f"expected {expected_status}/{expected_code}"
        )
    return error


def _ids(payload: dict[str, object]) -> set[int]:
    items = payload.get("items", [])
    return {int(item["id"]) for item in items if isinstance(item, dict)}


def run() -> dict[str, object]:
    run_id = uuid4().hex[:10]
    request_prefix = f"ann007-{run_id}"
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
        manager, relations = _manager_and_targets(connection)
        property_relations: dict[int, list[dict[str, object]]] = {}
        for relation in relations:
            property_relations.setdefault(int(relation["property_id"]), []).append(relation)
        property_id = max(
            property_relations,
            key=lambda item: len(property_relations[item]),
        )
        candidates = property_relations[property_id]
        matching = candidates[0]
        outsider = next(
            (
                item
                for item in candidates[1:]
                if int(item["unit_id"]) != int(matching["unit_id"])
            ),
            None,
        )
        tenant_other = next(
            (
                item
                for item in relations
                if int(item["property_id"]) != property_id
            ),
            None,
        )
        staff = _staff_target(
            connection,
            tenant_id=int(manager["tenant_id"]),
            property_id=property_id,
        )

    with httpx.Client(timeout=20.0) as client:
        health = client.get(f"{API}/healthz")
        if health.status_code != 200:
            raise RuntimeError(f"8083 health returned {health.status_code}")
        manager_session = _sms_login(
            client,
            phone=str(manager["phone_e164"]),
            request_prefix=f"{request_prefix}-manager",
        )
        manager_token = str(manager_session["access_token"])
        manager_me = _request(
            client,
            "GET",
            "/me",
            token=manager_token,
            request_id=f"{request_prefix}-manager-me",
        )
        if "announcement:publish" not in manager_me.get("permissions", []):
            raise RuntimeError("Manager session is missing announcement:publish")
        outsider_prepared_via_api = False
        outsider_session = None
        if outsider is None:
            if "property:manage" not in manager_me.get("permissions", []):
                raise RuntimeError("Manager cannot prepare a scoped synthetic outsider")
            building = _request(
                client,
                "POST",
                "/admin/buildings",
                token=manager_token,
                request_id=f"{request_prefix}-outside-building",
                json_body={
                    "tenant_id": int(manager["tenant_id"]),
                    "property_id": property_id,
                    "code": f"ANN{run_id.upper()}",
                    "name": f"公告验证楼栋 {run_id}",
                },
            )
            unit = _request(
                client,
                "POST",
                "/admin/units",
                token=manager_token,
                request_id=f"{request_prefix}-outside-unit",
                json_body={
                    "tenant_id": int(manager["tenant_id"]),
                    "property_id": property_id,
                    "building_id": building["id"],
                    "code": "U1",
                    "name": "公告验证单元",
                },
            )
            house = _request(
                client,
                "POST",
                "/admin/houses",
                token=manager_token,
                request_id=f"{request_prefix}-outside-house",
                json_body={
                    "tenant_id": int(manager["tenant_id"]),
                    "property_id": property_id,
                    "unit_id": unit["id"],
                    "room_no": "101",
                    "floor": 1,
                    "status": "ACTIVE",
                },
            )
            outsider_phone = f"+86137{int(uuid4().hex[:8], 16) % 100000000:08d}"
            outsider_session = _sms_login(
                client,
                phone=outsider_phone,
                request_prefix=f"{request_prefix}-outside-new",
            )
            bind_request = _request(
                client,
                "POST",
                "/resident-bind-requests",
                token=str(outsider_session["access_token"]),
                request_id=f"{request_prefix}-outside-bind",
                json_body={
                    "house_id": house["id"],
                    "tenant_id": int(manager["tenant_id"]),
                    "property_id": property_id,
                    "relation_type": "OWNER",
                },
            )
            _request(
                client,
                "POST",
                f"/admin/resident-bind-requests/{bind_request['id']}/approve",
                token=manager_token,
                request_id=f"{request_prefix}-outside-approve",
                json_body={},
            )
            outsider = {
                "user_id": outsider_session["user_id"],
                "phone_e164": outsider_phone,
                "property_id": property_id,
                "relation_type": "OWNER",
                "house_id": house["id"],
                "unit_id": unit["id"],
                "building_id": building["id"],
            }
            outsider_prepared_via_api = True

        title = f"ANN007 合成公告 {run_id}"
        draft = _request(
            client,
            "POST",
            "/admin/announcements",
            token=manager_token,
            request_id=f"{request_prefix}-draft",
            json_body={
                "property_id": property_id,
                "title": title,
                "content": "初稿：仅用于本地公告治理验证。",
                "scope_type": "PROPERTY",
                "audience": "RESIDENT",
            },
        )
        announcement_id = int(draft["id"])
        _expect_error(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=manager_token,
            request_id=f"{request_prefix}-early-publish",
            expected_status=409,
            expected_code="ANN.NOT_REVIEWED",
            json_body={"decision": "APPROVE"},
        )
        _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/submit-review",
            token=manager_token,
            request_id=f"{request_prefix}-submit-1",
            json_body={},
        )
        _expect_error(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=manager_token,
            request_id=f"{request_prefix}-reject-empty",
            expected_status=422,
            expected_code="ANN.INVALID_INPUT",
            json_body={"decision": "REJECT", "reason": ""},
        )
        rejected = _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=manager_token,
            request_id=f"{request_prefix}-reject",
            json_body={"decision": "REJECT", "reason": "请缩小到本轮目标单元"},
        )
        if rejected["status"] != "REJECTED":
            raise RuntimeError("Announcement rejection did not persist")
        revised = _request(
            client,
            "PATCH",
            f"/admin/announcements/{announcement_id}",
            token=manager_token,
            request_id=f"{request_prefix}-edit",
            json_body={
                "title": f"{title} 修订",
                "content": "修订稿：定向到本轮合成住户所在单元。",
                "scope_type": "UNIT",
                "building_id": int(matching["building_id"]),
                "unit_id": int(matching["unit_id"]),
                "audience": "RESIDENT",
            },
        )
        if revised["status"] != "DRAFT":
            raise RuntimeError("Editing a rejected announcement did not restore DRAFT")
        _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/submit-review",
            token=manager_token,
            request_id=f"{request_prefix}-submit-2",
            json_body={},
        )
        published = _request(
            client,
            "POST",
            f"/admin/announcements/{announcement_id}/publish",
            token=manager_token,
            request_id=f"{request_prefix}-approve",
            json_body={"decision": "APPROVE"},
        )
        if published["status"] != "PUBLISHED":
            raise RuntimeError("Announcement approval did not publish")
        detail = _request(
            client,
            "GET",
            f"/admin/announcements/{announcement_id}",
            token=manager_token,
            request_id=f"{request_prefix}-detail",
        )
        history_actions = [item["action"] for item in detail.get("history", [])]
        expected_actions = {
            "ANNOUNCEMENT_DRAFT_CREATED",
            "ANNOUNCEMENT_SUBMITTED",
            "ANNOUNCEMENT_REJECTED",
            "ANNOUNCEMENT_UPDATED",
            "ANNOUNCEMENT_PUBLISHED",
        }
        if not expected_actions.issubset(history_actions):
            raise RuntimeError("Announcement history is incomplete")
        review_decisions = [item["decision"] for item in detail.get("reviews", [])]
        if review_decisions[-2:] != ["REJECT", "APPROVE"]:
            raise RuntimeError("Review history did not preserve reject then approve")

        matching_session = _sms_login(
            client,
            phone=str(matching["phone_e164"]),
            request_prefix=f"{request_prefix}-matching-resident",
        )
        matching_list = _request(
            client,
            "GET",
            f"/announcements?property_id={property_id}",
            token=str(matching_session["access_token"]),
            request_id=f"{request_prefix}-matching-list",
        )
        if announcement_id not in _ids(matching_list):
            raise RuntimeError("Matching resident could not see the unit announcement")

        outsider_hidden = None
        if outsider is not None:
            if outsider_session is None:
                outsider_session = _sms_login(
                    client,
                    phone=str(outsider["phone_e164"]),
                    request_prefix=f"{request_prefix}-outsider",
                )
            outsider_list = _request(
                client,
                "GET",
                f"/announcements?property_id={property_id}",
                token=str(outsider_session["access_token"]),
                request_id=f"{request_prefix}-outsider-list",
            )
            outsider_hidden = announcement_id not in _ids(outsider_list)
            if not outsider_hidden:
                raise RuntimeError("A resident outside the target unit saw the announcement")

        tenant_announcement_id = None
        tenant_other_visible = None
        if manager["role_code"] == "TENANT_ADMIN":
            tenant_draft = _request(
                client,
                "POST",
                "/admin/announcements",
                token=manager_token,
                request_id=f"{request_prefix}-tenant-draft",
                json_body={
                    "property_id": property_id,
                    "title": f"ANN007 企业公告 {run_id}",
                    "content": "企业全部小区定向验证。",
                    "scope_type": "TENANT",
                    "audience": "RESIDENT",
                },
            )
            tenant_announcement_id = int(tenant_draft["id"])
            _request(
                client,
                "POST",
                f"/admin/announcements/{tenant_announcement_id}/submit-review",
                token=manager_token,
                request_id=f"{request_prefix}-tenant-submit",
                json_body={},
            )
            _request(
                client,
                "POST",
                f"/admin/announcements/{tenant_announcement_id}/publish",
                token=manager_token,
                request_id=f"{request_prefix}-tenant-approve",
                json_body={"decision": "APPROVE"},
            )
            if tenant_other is not None:
                other_session = _sms_login(
                    client,
                    phone=str(tenant_other["phone_e164"]),
                    request_prefix=f"{request_prefix}-tenant-other",
                )
                other_list = _request(
                    client,
                    "GET",
                    f"/announcements?property_id={tenant_other['property_id']}",
                    token=str(other_session["access_token"]),
                    request_id=f"{request_prefix}-tenant-other-list",
                )
                tenant_other_visible = tenant_announcement_id in _ids(other_list)
                if not tenant_other_visible:
                    raise RuntimeError("Tenant-wide announcement was hidden in another property")

        staff_announcement_id = None
        staff_visible = None
        resident_hidden_from_staff = None
        if staff is not None:
            staff_draft = _request(
                client,
                "POST",
                "/admin/announcements",
                token=manager_token,
                request_id=f"{request_prefix}-staff-draft",
                json_body={
                    "property_id": property_id,
                    "title": f"ANN007 员工公告 {run_id}",
                    "content": "仅当前小区员工可见。",
                    "scope_type": "AUDIENCE",
                    "audience": "STAFF",
                },
            )
            staff_announcement_id = int(staff_draft["id"])
            _request(
                client,
                "POST",
                f"/admin/announcements/{staff_announcement_id}/submit-review",
                token=manager_token,
                request_id=f"{request_prefix}-staff-submit",
                json_body={},
            )
            _request(
                client,
                "POST",
                f"/admin/announcements/{staff_announcement_id}/publish",
                token=manager_token,
                request_id=f"{request_prefix}-staff-approve",
                json_body={"decision": "APPROVE"},
            )
            staff_session = _sms_login(
                client,
                phone=str(staff["phone_e164"]),
                request_prefix=f"{request_prefix}-staff",
            )
            staff_list = _request(
                client,
                "GET",
                f"/announcements?property_id={property_id}",
                token=str(staff_session["access_token"]),
                request_id=f"{request_prefix}-staff-list",
            )
            staff_visible = staff_announcement_id in _ids(staff_list)
            resident_hidden_from_staff = staff_announcement_id not in _ids(matching_list)
            if not staff_visible or not resident_hidden_from_staff:
                raise RuntimeError("Staff audience visibility did not isolate correctly")

    with engine.connect() as connection:
        persisted = connection.execute(
            text(
                "SELECT id, status, title, reviewed_by_user_id, published_by_user_id "
                "FROM announcements WHERE id = :id"
            ),
            {"id": announcement_id},
        ).mappings().one()
        scopes = connection.execute(
            text(
                "SELECT scope_type, building_id, unit_id, audience "
                "FROM announcement_scopes WHERE announcement_id = :id ORDER BY id"
            ),
            {"id": announcement_id},
        ).mappings().all()
        reviews = connection.execute(
            text(
                "SELECT decision, reason, reviewer_user_id "
                "FROM announcement_reviews WHERE announcement_id = :id ORDER BY id"
            ),
            {"id": announcement_id},
        ).mappings().all()
        audits = connection.execute(
            text(
                "SELECT action, request_id FROM audit_logs "
                "WHERE resource_type = 'ANNOUNCEMENT' AND resource_id = :id ORDER BY id"
            ),
            {"id": announcement_id},
        ).mappings().all()
        notifications = connection.execute(
            text(
                "SELECT COUNT(DISTINCT user_id) FROM notifications "
                "WHERE resource_type = 'ANNOUNCEMENT' AND resource_id = :id"
            ),
            {"id": announcement_id},
        ).scalar_one()

    result = {
        "task_id": "ANNOUNCEMENT-GOVERNANCE-007",
        "run_id": run_id,
        "code_sha": "0eb299c325572acea4fdd2e9f8252f82cdcd2cc8",
        "environment": {
            "api": API,
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "migration": revision,
            "provider_boundary": "DEVELOPMENT SMS; no AI used",
        },
        "manager": {
            "user_id": manager["user_id"],
            "role_code": manager["role_code"],
            "phone_masked": mask_phone(str(manager["phone_e164"])),
            "official_sms_session": manager_session["auth_method"] == "sms",
        },
        "primary_announcement": {
            "id": announcement_id,
            "property_id": property_id,
            "matching_user_id": matching["user_id"],
            "matching_phone_masked": mask_phone(str(matching["phone_e164"])),
            "outside_target_available": outsider is not None,
            "outside_target_prepared_via_api": outsider_prepared_via_api,
            "outside_target_hidden": outsider_hidden,
            "api_history_actions": history_actions,
            "api_review_decisions": review_decisions,
            "persisted": dict(persisted),
            "scopes": [dict(item) for item in scopes],
            "reviews": [dict(item) for item in reviews],
            "audits": [dict(item) for item in audits],
            "notification_user_count": int(notifications),
        },
        "tenant_target": {
            "id": tenant_announcement_id,
            "other_property_available": tenant_other is not None,
            "other_property_visible": tenant_other_visible,
        },
        "staff_target": {
            "id": staff_announcement_id,
            "staff_available": staff is not None,
            "staff_visible": staff_visible,
            "resident_hidden": resident_hidden_from_staff,
        },
        "checks": {
            "early_publish_rejected": True,
            "reject_reason_required": True,
            "reject_edit_resubmit_publish": True,
            "review_history_preserved": True,
            "edit_publish_audit_history_preserved": True,
            "unit_target_matching_resident_visible": True,
            "unit_target_outsider_hidden": outsider_hidden,
            "tenant_scope_created": tenant_announcement_id is not None,
            "tenant_cross_property_verified": tenant_other_visible,
            "staff_audience_verified": staff_visible is True
            and resident_hidden_from_staff is True,
            "business_state_writes_via_api_only": True,
            "database_evidence_queries_read_only": True,
        },
    }
    return result


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
