"""Development Task 7 smoke: Fake AI success path plus AI-failure closed loop."""

from __future__ import annotations

import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import create_database_engine
from app.core.errors import AppError, DevSeedErrorCode
from app.domain.dev import DEV_PROPERTY_NAME
from app.main import create_app
from app.model.ai import Incident, WorkOrderAnalysis
from app.model.employee import Employee
from app.model.identity import User
from app.model.property import House, Property
from app.model.work_order import WorkOrder, WorkOrderContent
from app.provider.llm import DevelopmentLLMProvider
from app.service.analysis import AnalysisService
from app.service.dev_house_fixture import DEV_HOUSE_ROOM_NO
from app.service.dev_seed import require_dev_seed_environment

_BINARY_COLLATION = "utf8mb4_bin"


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": request_id}


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = get_settings()
    try:
        require_dev_seed_environment(settings)
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    if not settings.dev_auth_enabled:
        print(
            f"{DevSeedErrorCode.ENVIRONMENT_FORBIDDEN}: Development Auth is required",
            file=sys.stderr,
        )
        return 1

    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        resident = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_RESIDENT__"
            )
        )
        staff = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_STAFF__"
            )
        )
        manager = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION)
                == "__DEV_PROPERTY_MANAGER__"
            )
        )
        employee = None if staff is None else session.scalar(
            select(Employee).where(Employee.user_id == staff.id)
        )
        property_record = None if employee is None else session.scalar(
            select(Property).where(
                Property.tenant_id == employee.tenant_id,
                Property.name.collate(_BINARY_COLLATION) == DEV_PROPERTY_NAME,
            )
        )
        house = None if property_record is None else session.scalar(
            select(House).where(
                House.property_id == property_record.id,
                House.room_no.collate(_BINARY_COLLATION) == DEV_HOUSE_ROOM_NO,
            )
        )
        if None in {resident, staff, manager, employee, property_record, house}:
            print(
                f"{DevSeedErrorCode.DATA_CONFLICT}: Development AI fixture is missing",
                file=sys.stderr,
            )
            return 1
        ids = {
            "resident": resident.id,
            "staff": staff.id,
            "manager": manager.id,
            "employee": employee.id,
            "property": property_record.id,
            "house": house.id,
        }
    finally:
        session.close()

    with TestClient(create_app(settings)) as client:
        ready = client.get("/api/v1/readyz")
        resident_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["resident"]},
            headers={"X-Request-Id": "t7-smoke-resident-login"},
        ).json()["data"]["access_token"]
        staff_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["staff"]},
            headers={"X-Request-Id": "t7-smoke-staff-login"},
        ).json()["data"]["access_token"]
        manager_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["manager"]},
            headers={"X-Request-Id": "t7-smoke-manager-login"},
        ).json()["data"]["access_token"]
        client.post(
            "/api/v1/staff/attendance/checkin",
            json={"property_id": ids["property"]},
            headers=_auth(staff_token, "t7-smoke-checkin"),
        )
        image = client.post(
            "/api/v1/files",
            json={
                "mime_type": "image/jpeg",
                "size_bytes": 12,
                "property_id": ids["property"],
                "original_name": "outlet.jpg",
            },
            headers=_auth(resident_token, "t7-smoke-image"),
        )
        audio = client.post(
            "/api/v1/files",
            json={
                "mime_type": "audio/mpeg",
                "size_bytes": 12,
                "property_id": ids["property"],
                "original_name": "report.mp3",
            },
            headers=_auth(resident_token, "t7-smoke-audio"),
        )
        created = client.post(
            "/api/v1/work-orders",
            json={
                "house_id": ids["house"],
                "text": "Kitchen outlet is sparking",
                "file_ids": [
                    image.json()["data"]["id"],
                    audio.json()["data"]["id"],
                ],
            },
            headers={
                **_auth(resident_token, "t7-smoke-create"),
                "Idempotency-Key": f"t7-smoke-{uuid4()}",
            },
        )
        work_order_id = created.json().get("data", {}).get("id")
        analyze = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/analyze",
            json={},
            headers=_auth(manager_token, "t7-smoke-analyze"),
        )
        recommendations = client.get(
            f"/api/v1/supervisor/work-orders/{work_order_id}/recommendations",
            headers=_auth(manager_token, "t7-smoke-rec"),
        )
        assigned = client.post(
            f"/api/v1/supervisor/work-orders/{work_order_id}/assign",
            json={"employee_id": ids["employee"]},
            headers=_auth(manager_token, "t7-smoke-assign"),
        )
        extra_ids = []
        for index in range(4):
            extra = client.post(
                "/api/v1/work-orders",
                json={
                    "house_id": ids["house"],
                    "text": f"Kitchen outlet sparking {index}",
                },
                headers={
                    **_auth(resident_token, f"t7-smoke-extra-{index}"),
                    "Idempotency-Key": f"t7-smoke-extra-{uuid4()}",
                },
            )
            extra_id = extra.json().get("data", {}).get("id")
            extra_ids.append(extra_id)
            client.post(
                f"/api/v1/supervisor/work-orders/{extra_id}/analyze",
                json={},
                headers=_auth(manager_token, f"t7-smoke-extra-analyze-{index}"),
            )
        incidents = client.get(
            "/api/v1/supervisor/incidents",
            params={"property_id": ids["property"]},
            headers=_auth(manager_token, "t7-smoke-incidents"),
        )
        incident_id = (incidents.json().get("data", {}).get("items") or [{}])[0].get("id")
        summary = None
        if incident_id:
            summary = client.post(
                f"/api/v1/supervisor/incidents/{incident_id}/summary",
                json={},
                headers=_auth(manager_token, "t7-smoke-summary"),
            )
        draft = client.post(
            "/api/v1/admin/announcements",
            json={
                "property_id": ids["property"],
                "incident_id": incident_id,
                "ai_draft": True,
            },
            headers=_auth(manager_token, "t7-smoke-draft"),
        )
        announcement_id = draft.json().get("data", {}).get("id")
        client.post(
            f"/api/v1/admin/announcements/{announcement_id}/submit-review",
            json={},
            headers=_auth(manager_token, "t7-smoke-submit"),
        )
        published = client.post(
            f"/api/v1/admin/announcements/{announcement_id}/publish",
            json={},
            headers=_auth(manager_token, "t7-smoke-publish"),
        )
        visible = client.get(
            "/api/v1/announcements",
            params={"property_id": ids["property"]},
            headers=_auth(resident_token, "t7-smoke-read"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/accept",
            json={},
            headers=_auth(staff_token, "t7-smoke-accept"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/start",
            json={},
            headers=_auth(staff_token, "t7-smoke-start"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{work_order_id}/receipts",
            json={
                "problem_type": "ELECTRICAL",
                "method": "Replace outlet",
                "resolved": True,
            },
            headers=_auth(staff_token, "t7-smoke-receipt"),
        )
        client.post(
            f"/api/v1/work-orders/{work_order_id}/confirm",
            json={},
            headers=_auth(resident_token, "t7-smoke-confirm"),
        )
        failed_create = client.post(
            "/api/v1/work-orders",
            json={"house_id": ids["house"], "text": "AI should fail"},
            headers={
                **_auth(resident_token, "t7-smoke-fail-create"),
                "Idempotency-Key": f"t7-smoke-fail-{uuid4()}",
            },
        )
        failed_id = failed_create.json().get("data", {}).get("id")

    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        AnalysisService(
            session,
            settings=settings,
            llm=DevelopmentLLMProvider("timeout"),
        ).analyze_work_order(work_order_id=failed_id, request_id="t7-smoke-fail-ai")
        failed_order = session.get(WorkOrder, failed_id)
        failed_analysis = session.scalar(
            select(WorkOrderAnalysis)
            .where(WorkOrderAnalysis.work_order_id == failed_id)
            .order_by(WorkOrderAnalysis.id.desc())
        )
        content = session.scalar(
            select(WorkOrderContent).where(WorkOrderContent.work_order_id == work_order_id)
        )
        analysis = session.scalar(
            select(WorkOrderAnalysis).where(
                WorkOrderAnalysis.work_order_id == work_order_id
            )
        )
        incident_count = session.scalar(select(Incident.id).limit(1))
    finally:
        session.close()

    with TestClient(create_app(settings)) as client:
        assigned_fail = client.post(
            f"/api/v1/supervisor/work-orders/{failed_id}/assign",
            json={"employee_id": ids["employee"]},
            headers=_auth(manager_token, "t7-smoke-fail-assign"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{failed_id}/accept",
            json={},
            headers=_auth(staff_token, "t7-smoke-fail-accept"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{failed_id}/start",
            json={},
            headers=_auth(staff_token, "t7-smoke-fail-start"),
        )
        client.post(
            f"/api/v1/staff/work-orders/{failed_id}/receipts",
            json={
                "problem_type": "ELECTRICAL",
                "method": "Replace outlet",
                "resolved": True,
            },
            headers=_auth(staff_token, "t7-smoke-fail-receipt"),
        )
        confirmed = client.post(
            f"/api/v1/work-orders/{failed_id}/confirm",
            json={},
            headers=_auth(resident_token, "t7-smoke-fail-confirm"),
        )

    engine.dispose()
    rec_ids = [
        item["employee_id"]
        for item in recommendations.json().get("data", {}).get("items", [])
    ]
    named_checks = (
        ("readyz", ready.status_code == 200 and ready.json()["data"]["status"] in {"ready", "degraded"}),
        ("create", created.status_code == 200 and created.json()["data"]["status"] == "PENDING_ANALYSIS"),
        ("analyze", analyze.status_code == 200),
        ("asr_text", content is not None and bool(content.asr_text)),
        ("analysis_category", analysis is not None and analysis.category == "ELECTRICAL"),
        ("recommendations", rec_ids == [ids["employee"]]),
        ("assign", assigned.status_code == 200),
        ("incident", incident_count is not None),
        ("summary", summary is None or summary.status_code == 200),
        ("draft", draft.status_code == 200 and draft.json()["data"]["status"] == "DRAFT"),
        ("publish", published.status_code == 200 and published.json()["data"]["status"] == "PUBLISHED"),
        ("resident_read", visible.status_code == 200 and visible.json()["data"]["items"]),
        ("fail_create", failed_create.status_code == 200),
        ("fail_kept", failed_order is not None and failed_order.status == "PENDING_ANALYSIS"),
        ("fail_analysis", failed_analysis is not None and failed_analysis.status == "FAILED"),
        ("fail_assign", assigned_fail.status_code == 200),
        (
            "fail_closed_loop",
            confirmed.status_code == 200
            and confirmed.json()["data"]["status"] == "CLOSED",
        ),
        (
            "ai_not_critical",
            ready.json()["data"]["dependencies"].get("llm", {}).get("critical") is False,
        ),
    )
    failed = [name for name, ok in named_checks if not ok]
    if failed:
        print("Development AI smoke failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(
        "Development AI smoke passed "
        f"work_order_id={work_order_id} announcement_id={announcement_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
