"""Read-only DB binding for the 013 WeChat DevTools page run."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import site
import subprocess
import sys
from urllib.parse import urlsplit


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = REPO / "scripts" / "ops"
for candidate in (BACKEND, OPS):
    sys.path.insert(0, str(candidate))
site.addsitedir(str(REPO / ".venv" / "Lib" / "site-packages"))

from sqlalchemy import bindparam, create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from auth001_isolated_runtime import (  # noqa: E402
    ISOLATED_QUEUE,
    ISOLATED_VHOST,
    build_isolated_settings,
)


TASK_ID = "NOTIFICATION-EVENTS-INBOX-013"
PRIVATE = REPO / "tmp" / "notification-events-inbox-013" / "context.json"
PAGE = Path(__file__).with_name("page-evidence.json")
RUNTIME = Path(__file__).with_name("runtime-evidence.json")
OUTPUT = Path(__file__).with_name("post-page-database-evidence.json")


def main() -> None:
    private = json.loads(PRIVATE.read_text(encoding="utf-8"))
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8"))
    if private["run_id"] != page["run_id"] or page["run_id"] != runtime["run_id"]:
        raise RuntimeError("Page/runtime/private evidence run mismatch")

    settings = build_isolated_settings()
    database = make_url(settings.test_database_url)
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
        raise RuntimeError(f"AUTH001 guard mismatch: {actual!r}")

    work_notification_id = int(page["objects"]["work_order_notification_id"])
    announcement_notification_id = int(page["objects"]["announcement_notification_id"])
    work_order_id = int(page["objects"]["work_order_id"])
    announcement_id = int(page["objects"]["announcement_id"])
    resident_user_id = int(private["identities"]["primary"])
    tenant_id = int(private["org"]["tenant_id"])
    property_id = int(private["org"]["property_id"])

    engine = create_engine(settings.test_database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            statement = text(
                "SELECT id, user_id, tenant_id, property_id, resource_type, resource_id, "
                "read_at, created_at FROM notifications WHERE id IN :ids ORDER BY id"
            ).bindparams(bindparam("ids", expanding=True))
            notifications = [
                dict(row)
                for row in connection.execute(
                    statement,
                    {"ids": (work_notification_id, announcement_notification_id)},
                ).mappings()
            ]
            work_order = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, reporter_user_id, status, version, "
                        "closed_at FROM work_orders WHERE id=:id"
                    ),
                    {"id": work_order_id},
                ).mappings().one()
            )
            announcement = dict(
                connection.execute(
                    text(
                        "SELECT id, tenant_id, property_id, status, published_at "
                        "FROM announcements WHERE id=:id"
                    ),
                    {"id": announcement_id},
                ).mappings().one()
            )
            history = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT event_code, from_status, to_status, actor_user_id, created_at "
                        "FROM work_order_logs WHERE work_order_id=:id ORDER BY id"
                    ),
                    {"id": work_order_id},
                ).mappings()
            ]
    finally:
        engine.dispose()

    if revision != "20260919_0009":
        raise RuntimeError("AUTH001 revision changed")
    if len(notifications) != 2 or any(row["read_at"] is None for row in notifications):
        raise RuntimeError("Both page-opened notifications must be persistently read")
    expected_resources = {
        (work_notification_id, "WORK_ORDER", work_order_id),
        (announcement_notification_id, "ANNOUNCEMENT", announcement_id),
    }
    actual_resources = {
        (int(row["id"]), str(row["resource_type"]), int(row["resource_id"]))
        for row in notifications
    }
    if actual_resources != expected_resources:
        raise RuntimeError("Page notification/resource mapping changed")
    for row in notifications:
        if (
            int(row["user_id"]) != resident_user_id
            or int(row["tenant_id"]) != tenant_id
            or int(row["property_id"]) != property_id
        ):
            raise RuntimeError("Page notification ownership or scope changed")
    if work_order["status"] != "CLOSED" or announcement["status"] != "PUBLISHED":
        raise RuntimeError("Referenced business history is no longer final")

    evidence = {
        "task_id": TASK_ID,
        "captured_at": datetime.now(UTC).isoformat(),
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "run_id": private["run_id"],
        "environment": {
            "database": "127.0.0.1:3307/property_ai_auth001_test",
            "revision": revision,
            "access": "SET TRANSACTION READ ONLY",
            "rabbit_vhost": ISOLATED_VHOST,
            "rabbit_queue": ISOLATED_QUEUE,
        },
        "notifications": notifications,
        "work_order": work_order,
        "announcement": announcement,
        "work_order_history": history,
        "checks": {
            "same_page_run": True,
            "both_read_at_persisted": True,
            "same_resident_owner": True,
            "same_tenant_property_scope": True,
            "work_order_notification_resource_matches": True,
            "announcement_notification_resource_matches": True,
            "work_order_remains_closed": True,
            "announcement_remains_published": True,
        },
        "boundaries": {
            "database_business_write": False,
            "migration_run": False,
            "shared_vhost_touched": False,
            "production_claimed": False,
        },
    }
    OUTPUT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "evidence": str(OUTPUT.relative_to(REPO))}))


if __name__ == "__main__":
    main()
