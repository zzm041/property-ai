"""MySQL restore with relational integrity on the disposable test instance."""

from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repository.identity import IdentityRepository
from app.service.identity.auth import AuthService
from app.service.employee import EmployeeService
from app.service.work_order import WorkOrderService
from tests.integration.test_day3_backup_restore import (
    RESTORE_DB,
    _docker_available,
    _exec,
    _mysql_test_container,
)
from tests.integration.conftest import commit_open_transaction
from tests.integration.work_order_test_data import create_work_order_graph


@pytest.mark.skipif(not _docker_available(), reason="docker is not available")
def test_mysql_restore_preserves_relations_orders_and_audit(
    identity_session: Session,
) -> None:
    container = _mysql_test_container()
    if container is None:
        pytest.skip("mysql-test container is not running")

    graph = create_work_order_graph(identity_session)
    principal = AuthService(
        identity_session, IdentityRepository(identity_session)
    ).load_principal(graph.employees.resident_id)
    staff = AuthService(
        identity_session, IdentityRepository(identity_session)
    ).load_principal(graph.employees.staff_a_user_id)
    commit_open_transaction(identity_session)
    EmployeeService(identity_session).check_in(
        actor=staff,
        property_id=graph.employees.property_1_id,
        request_id="d4-restore-checkin",
    )
    created = WorkOrderService(identity_session).create_work_order(
        actor=principal,
        house_id=graph.house_1_id,
        contact_phone="13800000000",
        request_id="d4-restore-wo",
        idempotency_key=str(uuid4()),
        text="Restore drill work order",
    )
    manager = AuthService(
        identity_session, IdentityRepository(identity_session)
    ).load_principal(graph.employees.manager_p1_id)
    commit_open_transaction(identity_session)
    WorkOrderService(identity_session).assign(
        actor=manager,
        work_order_id=created.id,
        employee_id=graph.employees.employee_a_id,
        request_id="d4-restore-assign",
    )
    identity_session.expire_all()
    counts = {}
    for table in (
        "users",
        "houses",
        "resident_house_relations",
        "work_orders",
        "work_order_assignments",
        "work_order_logs",
        "audit_logs",
        "employees",
    ):
        counts[table] = int(
            identity_session.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        )
    revision = identity_session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    assert counts["houses"] >= 1
    assert counts["resident_house_relations"] >= 1
    assert counts["users"] >= 1
    assert counts["work_orders"] >= 1
    assert counts["work_order_assignments"] >= 1
    assert counts["work_order_logs"] >= 1
    assert counts["audit_logs"] >= 1
    assert graph.house_1_id > 0

    unique_marker = f"day4-object-bytes-not-in-mysql-{uuid4().hex}"
    payload = unique_marker.encode("utf-8")
    digest = sha256(payload).hexdigest()
    object_key = None
    minio_provider = None
    for endpoint in ("http://127.0.0.1:9000", "http://127.0.0.1:9010"):
        try:
            live = httpx.get(f"{endpoint}/minio/health/live", timeout=2.0)
        except httpx.HTTPError:
            continue
        if live.status_code != 200:
            continue
        from app.provider.storage import ProductionStorageProvider

        minio_provider = ProductionStorageProvider(
            endpoint=endpoint,
            bucket="property-ai-day4-restore",
            access_key="minioadmin",
            secret_key="minioadmin",
            region="us-east-1",
            presign_ttl_seconds=60,
        )
        minio_provider.ensure_bucket()
        registered = minio_provider.register_object(
            mime_type="image/jpeg",
            size_bytes=len(payload),
            tenant_id=1,
            property_id=1,
        )
        object_key = registered.storage_key
        minio_provider.put_object(object_key, payload, "image/jpeg")
        assert sha256(minio_provider.get_object(object_key)).hexdigest() == digest
        break
    if object_key is None:
        pytest.fail("Compose MinIO was not reachable for object-checksum proof")

    dump = _exec(
        container,
        [
            "mysqldump",
            "--single-transaction",
            "-uroot",
            "-pchange-me-test-root",
            "property_ai_test",
        ],
    )
    assert dump.returncode == 0, dump.stderr
    assert unique_marker not in dump.stdout

    prepared = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            "-e",
            f"DROP DATABASE IF EXISTS `{RESTORE_DB}`; CREATE DATABASE `{RESTORE_DB}` CHARACTER SET utf8mb4;",
        ],
    )
    assert prepared.returncode == 0, prepared.stderr
    restored = _exec(
        container,
        ["mysql", "-uroot", "-pchange-me-test-root", RESTORE_DB],
        input_text=dump.stdout,
    )
    assert restored.returncode == 0, restored.stderr

    select_sql = (
        "SELECT version_num FROM alembic_version;"
        + "".join(f"SELECT COUNT(*) FROM `{table}`;" for table in counts)
    )
    verify = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            RESTORE_DB,
            "-N",
            "-e",
            select_sql,
        ],
    )
    assert verify.returncode == 0, verify.stderr
    lines = [line.strip() for line in verify.stdout.splitlines() if line.strip()]
    assert lines[0] == revision
    restored_counts = [int(item) for item in lines[1:]]
    assert restored_counts == list(counts.values())

    relation_sql = (
        f"SELECT COUNT(*) FROM work_orders wo "
        f"JOIN work_order_assignments a ON a.work_order_id = wo.id "
        f"JOIN work_order_logs l ON l.work_order_id = wo.id "
        f"JOIN houses h ON h.id = wo.house_id "
        f"JOIN resident_house_relations r ON r.house_id = h.id "
        f"AND r.user_id = wo.reporter_user_id "
        f"WHERE wo.id = {created.id};"
        f"SELECT COUNT(*) FROM audit_logs WHERE resource_id = {created.id} "
        f"OR request_id IN ('d4-restore-wo','d4-restore-assign','d4-restore-checkin');"
    )
    related = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            RESTORE_DB,
            "-N",
            "-e",
            relation_sql,
        ],
    )
    assert related.returncode == 0, related.stderr
    related_lines = [line.strip() for line in related.stdout.splitlines() if line.strip()]
    assert int(related_lines[0]) >= 1
    assert int(related_lines[1]) >= 1

    restored_dump_check = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            RESTORE_DB,
            "-N",
            "-e",
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{RESTORE_DB}';",
        ],
    )
    assert restored_dump_check.returncode == 0
    assert unique_marker not in restored_dump_check.stdout
    assert minio_provider is not None and object_key is not None
    fetched = minio_provider.get_object(object_key)
    assert sha256(fetched).hexdigest() == digest
    assert fetched == payload

    _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            "-e",
            f"DROP DATABASE IF EXISTS `{RESTORE_DB}`",
        ],
    )
