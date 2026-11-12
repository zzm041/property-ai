"""F-4 / t210: the announcement review chain must be reachable by a supervisor.

Three things are pinned here:

* the role matrix gives SUPERVISOR `announcement:publish` without handing it to
  STAFF/RESIDENT (and without taking it away from the admin roles);
* the publish service refuses an actor that only holds `announcement:draft`, with
  the IAM 403 the miniprogram renders as "no permission";
* the migration that ships the grant is idempotent and only removes the role
  grant on downgrade, never the shared permission row.
"""

from __future__ import annotations

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.cli import delivery_migrate
from app.core.errors import AppError, IamErrorCode
from app.domain.ai import ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH, ANNOUNCEMENT_READ
from app.domain.dev import SEED_ROLE_PERMISSIONS
from app.domain.governance import AuditAction
from app.domain.identity import OrganizationScope, Principal, RoleGrant, ScopeType
from app.model.ai import Announcement, AnnouncementScope
from app.repository.ai import AiRepository
from app.repository.audit import AuditRepository
from app.repository.employee import EmployeeRepository
from app.repository.property import PropertyRepository
from app.repository.resident import ResidentBindingRepository
from app.repository.work_order import WorkOrderRepository
from app.service.announcement import AnnouncementService

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "20261112_0020_supervisor_announcement_publish.py"


def _principal(*permissions: str, role_code: str = "SUPERVISOR") -> Principal:
    return Principal(
        user_id=22,
        status="ACTIVE",
        display_name="Supervisor",
        grants=(
            RoleGrant(
                role_code=role_code,
                scope=OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=10),
                permissions=frozenset(permissions),
            ),
        ),
    )


def _service() -> tuple[AnnouncementService, MagicMock]:
    session = MagicMock()
    session.in_transaction.return_value = False
    session.info = {}
    service = AnnouncementService(
        session,
        settings=MagicMock(),
        llm=MagicMock(),
        clock=lambda: None,
    )
    service._ai = MagicMock(spec=AiRepository)
    service._audit_repository = MagicMock(spec=AuditRepository)
    service._employees = MagicMock(spec=EmployeeRepository)
    service._properties = MagicMock(spec=PropertyRepository)
    service._residents = MagicMock(spec=ResidentBindingRepository)
    service._work_orders = MagicMock(spec=WorkOrderRepository)
    service._audit = MagicMock()
    return service, session


def _announcement(status: str = "PENDING_REVIEW") -> Announcement:
    return Announcement(
        id=31,
        tenant_id=1,
        property_id=10,
        incident_id=None,
        title="Original",
        content="Original content",
        status=status,
        ai_draft=0,
        created_by_user_id=22,
    )


def _scope() -> AnnouncementScope:
    return AnnouncementScope(
        id=41,
        tenant_id=1,
        property_id=10,
        announcement_id=31,
        scope_type="PROPERTY",
        building_id=None,
        unit_id=None,
        audience="RESIDENT",
    )


def test_supervisor_role_carries_the_publish_permission() -> None:
    supervisor = set(SEED_ROLE_PERMISSIONS["SUPERVISOR"])
    assert ANNOUNCEMENT_DRAFT in supervisor
    assert ANNOUNCEMENT_PUBLISH in supervisor
    assert ANNOUNCEMENT_READ in supervisor


def test_publish_permission_is_not_handed_to_operational_roles() -> None:
    assert ANNOUNCEMENT_PUBLISH not in set(SEED_ROLE_PERMISSIONS["STAFF"])
    assert ANNOUNCEMENT_PUBLISH not in set(SEED_ROLE_PERMISSIONS["RESIDENT"])
    # The admin roles keep what they had.
    assert ANNOUNCEMENT_PUBLISH in set(SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"])
    assert ANNOUNCEMENT_PUBLISH in set(SEED_ROLE_PERMISSIONS["TENANT_ADMIN"])


def test_draft_only_actor_cannot_publish(monkeypatch) -> None:
    service, _ = _service()
    service._ai.get_announcement_for_update.return_value = _announcement()
    service._ai.list_announcement_scopes.return_value = (_scope(),)
    monkeypatch.setattr(
        "app.service.announcement.begin_owned_transaction",
        lambda session: nullcontext(),
    )

    with pytest.raises(AppError) as captured:
        service.publish(
            actor=_principal(ANNOUNCEMENT_DRAFT),
            announcement_id=31,
            request_id="t210-forbidden",
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    assert captured.value.status_code == 403
    service._ai.add_announcement_review.assert_not_called()


def test_supervisor_can_publish_a_reviewed_announcement(monkeypatch) -> None:
    service, _ = _service()
    service._ai.get_announcement_for_update.return_value = _announcement()
    service._ai.list_announcement_scopes.return_value = (_scope(),)
    monkeypatch.setattr(
        "app.service.announcement.begin_owned_transaction",
        lambda session: nullcontext(),
    )

    result = service.publish(
        actor=_principal(ANNOUNCEMENT_DRAFT, ANNOUNCEMENT_PUBLISH),
        announcement_id=31,
        request_id="t210-publish",
    )

    assert result.status == "PUBLISHED"
    review = service._ai.add_announcement_review.call_args.args[0]
    assert review.decision == "APPROVE"
    assert service._audit.append.call_args.kwargs["action"] is AuditAction.ANNOUNCEMENT_PUBLISHED


def _migration_module():
    spec = importlib.util.spec_from_file_location("t210_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_idempotent_and_only_reverts_the_role_grant() -> None:
    module = _migration_module()
    assert module.revision == "20261112_0020_supervisor_announcement_publish"
    assert module.PERMISSION == "announcement:publish"
    assert module.ROLE == "SUPERVISOR"
    # A database migrated twice, or one seeded through app.domain.dev, must not
    # end up with a duplicate row (permissions.code / role_permissions are unique).
    assert "WHERE NOT EXISTS" in module.ENSURE_PERMISSION
    assert "NOT EXISTS" in module.ENSURE_GRANT
    # Downgrade removes the grant only: the permission row is shared with
    # PROPERTY_ADMIN and TENANT_ADMIN.
    assert "DELETE rp FROM role_permissions rp" in module.DELETE_GRANT
    assert "permissions" in module.DELETE_GRANT


def test_delivery_migrator_can_resume_from_the_previous_head() -> None:
    module = _migration_module()
    assert module.down_revision == "20261111_0019_ai_call_log_embedding_rerank"
    assert module.down_revision in delivery_migrate.RESUMABLE_REVISIONS
    assert delivery_migrate.HEAD == module.revision
