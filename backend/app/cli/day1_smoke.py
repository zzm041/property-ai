"""Day 1 end-to-end Development smoke: WeChat, knowledge RAG, ACL, notifications."""

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
from app.model.identity import User
from app.model.property import Property
from app.service.dev_seed import require_dev_seed_environment
from app.service.knowledge import KnowledgeService

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
    engine = create_database_engine(settings.active_database_url)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        resident = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_RESIDENT__"
            )
        )
        manager = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION)
                == "__DEV_PROPERTY_MANAGER__"
            )
        )
        staff = session.scalar(
            select(User).where(
                User.display_name.collate(_BINARY_COLLATION) == "__DEV_STAFF__"
            )
        )
        property_record = session.scalar(
            select(Property).where(
                Property.name.collate(_BINARY_COLLATION) == DEV_PROPERTY_NAME
            )
        )
        if None in {resident, manager, staff, property_record}:
            print(
                f"{DevSeedErrorCode.DATA_CONFLICT}: Development seed/fixture is missing",
                file=sys.stderr,
            )
            return 1
        ids = {
            "resident": resident.id,
            "manager": manager.id,
            "staff": staff.id,
            "property": property_record.id,
        }
    finally:
        session.close()

    with TestClient(create_app(settings)) as client:
        wechat = client.post(
            "/api/v1/auth/wechat/login",
            json={"code": f"wx-new-{uuid4().hex[:8]}"},
            headers={"X-Request-Id": "day1-wx"},
        )
        if wechat.status_code != 200:
            print(f"WeChat login failed: {wechat.text}", file=sys.stderr)
            return 1
        me = client.get(
            "/api/v1/me",
            headers=_auth(wechat.json()["data"]["access_token"], "day1-wx-me"),
        )
        if me.status_code != 200:
            print(f"/me failed: {me.text}", file=sys.stderr)
            return 1
        manager_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["manager"]},
            headers={"X-Request-Id": "day1-mgr"},
        ).json()["data"]["access_token"]
        created = client.post(
            "/api/v1/admin/knowledge-documents",
            json={
                "property_id": ids["property"],
                "title": "装修规定",
                "document_type": "RULE",
                "visibility": "RESIDENT",
                "source_text": "装修施工时间为工作日 08:00-18:00，周末及法定节假日不得施工。",
            },
            headers=_auth(manager_token, "day1-kb-create"),
        )
        if created.status_code != 200:
            print(f"knowledge create failed: {created.text}", file=sys.stderr)
            return 1
        payload = created.json()["data"]
        ingest_session = Session(bind=engine, autoflush=False, expire_on_commit=False)
        try:
            KnowledgeService(ingest_session, settings=settings).ingest_version(
                document_id=payload["document_id"],
                version=payload["version"],
                request_id="day1-ingest",
            )
        finally:
            ingest_session.close()
        resident_token = client.post(
            "/api/v1/auth/dev/token",
            json={"user_id": ids["resident"]},
            headers={"X-Request-Id": "day1-res"},
        ).json()["data"]["access_token"]
        asked = client.post(
            "/api/v1/assistant/query",
            json={
                "question": "装修时间是什么？",
                "property_id": ids["property"],
            },
            headers=_auth(resident_token, "day1-ask"),
        )
        if asked.status_code != 200 or asked.json()["data"]["status"] != "answered":
            print(f"RAG query failed: {asked.text}", file=sys.stderr)
            return 1
        if not asked.json()["data"]["citations"]:
            print("RAG query missing citations", file=sys.stderr)
            return 1
        notes = client.get(
            "/api/v1/notifications",
            headers=_auth(resident_token, "day1-ntf"),
        )
        if notes.status_code != 200:
            print(f"notifications failed: {notes.text}", file=sys.stderr)
            return 1
        health = client.get("/api/v1/healthz")
        ready = client.get("/api/v1/readyz")
        if health.status_code != 200 or ready.status_code != 200:
            print(f"health failed healthz={health.status_code} readyz={ready.status_code}", file=sys.stderr)
            return 1
    print("Day 1 smoke PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
