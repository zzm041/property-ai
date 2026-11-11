"""Append-only Audit Runtime behavior on disposable MySQL 8."""

from dataclasses import dataclass
import json

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.governance import AuditAction, AuditResourceType
from app.domain.identity import OrganizationScope, ScopeType
from app.model.identity import AuditLog, User
from app.model.property import Property, Tenant
from app.repository.audit import AuditRepository
from app.service.audit import AuditService


@dataclass(frozen=True, slots=True)
class AuditTestGraph:
    actor_user_id: int
    target_user_id: int
    tenant_1_id: int
    tenant_2_id: int
    property_1_id: int
    property_2_id: int
    property_3_id: int


def _create_graph(session: Session) -> AuditTestGraph:
    with session.begin():
        tenant_1 = Tenant(name="C1 Tenant 1", status="ACTIVE")
        tenant_2 = Tenant(name="C1 Tenant 2", status="ACTIVE")
        session.add_all((tenant_1, tenant_2))
        session.flush()
        property_1 = Property(
            tenant_id=tenant_1.id,
            name="C1 Property 1",
            address="C1 Address 1",
            timezone="Asia/Shanghai",
        )
        property_2 = Property(
            tenant_id=tenant_1.id,
            name="C1 Property 2",
            address="C1 Address 2",
            timezone="Asia/Shanghai",
        )
        property_3 = Property(
            tenant_id=tenant_2.id,
            name="C1 Property 3",
            address="C1 Address 3",
            timezone="Asia/Shanghai",
        )
        actor = User(status="ACTIVE", display_name="C1 Actor")
        target = User(status="ACTIVE", display_name="C1 Target")
        session.add_all((property_1, property_2, property_3, actor, target))
        session.flush()

    return AuditTestGraph(
        actor_user_id=actor.id,
        target_user_id=target.id,
        tenant_1_id=tenant_1.id,
        tenant_2_id=tenant_2.id,
        property_1_id=property_1.id,
        property_2_id=property_2.id,
        property_3_id=property_3.id,
    )


def _user_role_details(
    graph: AuditTestGraph,
    scope_type: ScopeType,
) -> dict[str, object]:
    return {
        "target_user_id": graph.target_user_id,
        "role_code": "PROPERTY_ADMIN",
        "scope_type": scope_type.value,
        "source": "INTERNAL",
    }


def _append_user_role_event(
    service: AuditService,
    graph: AuditTestGraph,
    *,
    request_id: str,
    resource_id: int,
    scope_type: ScopeType,
    tenant_id: int | None = None,
    property_id: int | None = None,
) -> AuditLog:
    return service.append(
        action=AuditAction.IAM_USER_ROLE_GRANTED,
        resource_type=AuditResourceType.USER_ROLE,
        actor_user_id=graph.actor_user_id,
        resource_id=resource_id,
        request_id=request_id,
        scope_type=scope_type,
        tenant_id=tenant_id,
        property_id=property_id,
        details=_user_role_details(graph, scope_type),
    )


def test_append_persists_request_id_and_all_scope_shapes(
    identity_session: Session,
) -> None:
    graph = _create_graph(identity_session)
    service = AuditService(identity_session)

    with identity_session.begin():
        system = _append_user_role_event(
            service,
            graph,
            request_id="c1-system",
            resource_id=101,
            scope_type=ScopeType.SYSTEM,
        )
        tenant = _append_user_role_event(
            service,
            graph,
            request_id="c1-tenant",
            resource_id=102,
            scope_type=ScopeType.TENANT,
            tenant_id=graph.tenant_1_id,
        )
        property_event = _append_user_role_event(
            service,
            graph,
            request_id="c1-property",
            resource_id=103,
            scope_type=ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        ids = (system.id, tenant.id, property_event.id)

    records = tuple(
        identity_session.get(AuditLog, audit_id) for audit_id in ids
    )
    assert all(record is not None for record in records)
    assert records[0].request_id == "c1-system"
    assert (records[0].tenant_id, records[0].property_id) == (None, None)
    assert records[1].request_id == "c1-tenant"
    assert (records[1].tenant_id, records[1].property_id) == (
        graph.tenant_1_id,
        None,
    )
    assert records[2].request_id == "c1-property"
    assert (records[2].tenant_id, records[2].property_id) == (
        graph.tenant_1_id,
        graph.property_1_id,
    )


def test_property_tenant_mismatch_is_rejected_by_mysql(
    identity_session: Session,
) -> None:
    graph = _create_graph(identity_session)
    service = AuditService(identity_session)

    with pytest.raises(DBAPIError) as captured:
        with identity_session.begin():
            _append_user_role_event(
                service,
                graph,
                request_id="c1-cross-tenant",
                resource_id=201,
                scope_type=ScopeType.PROPERTY,
                tenant_id=graph.tenant_2_id,
                property_id=graph.property_1_id,
            )

    assert getattr(captured.value.orig, "args", [None])[0] == 1452
    assert identity_session.query(AuditLog).count() == 0


def test_repository_reads_are_strictly_scope_filtered(
    identity_session: Session,
) -> None:
    graph = _create_graph(identity_session)
    service = AuditService(identity_session)
    repository = AuditRepository(identity_session)

    with identity_session.begin():
        system = _append_user_role_event(
            service,
            graph,
            request_id="shared-request",
            resource_id=301,
            scope_type=ScopeType.SYSTEM,
        )
        tenant_1 = _append_user_role_event(
            service,
            graph,
            request_id="shared-request",
            resource_id=302,
            scope_type=ScopeType.TENANT,
            tenant_id=graph.tenant_1_id,
        )
        property_1 = _append_user_role_event(
            service,
            graph,
            request_id="shared-request",
            resource_id=303,
            scope_type=ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        property_2 = _append_user_role_event(
            service,
            graph,
            request_id="another-request",
            resource_id=304,
            scope_type=ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_2_id,
        )
        tenant_2 = _append_user_role_event(
            service,
            graph,
            request_id="shared-request",
            resource_id=305,
            scope_type=ScopeType.TENANT,
            tenant_id=graph.tenant_2_id,
        )
        property_3 = _append_user_role_event(
            service,
            graph,
            request_id="shared-request",
            resource_id=306,
            scope_type=ScopeType.PROPERTY,
            tenant_id=graph.tenant_2_id,
            property_id=graph.property_3_id,
        )

    assert [row.id for row in repository.list_system_scope()] == [
        system.id,
        tenant_1.id,
        property_1.id,
        property_2.id,
        tenant_2.id,
        property_3.id,
    ]
    assert [row.id for row in repository.list_tenant_scope(
        tenant_id=graph.tenant_1_id
    )] == [tenant_1.id, property_1.id, property_2.id]
    assert [row.id for row in repository.list_property_scope(
        tenant_id=graph.tenant_1_id,
        property_id=graph.property_1_id,
    )] == [property_1.id]
    assert repository.list_property_scope(
        tenant_id=graph.tenant_2_id,
        property_id=graph.property_1_id,
    ) == []
    assert [row.id for row in repository.list_by_request_id(
        request_id="shared-request",
        scope=OrganizationScope(
            ScopeType.TENANT,
            tenant_id=graph.tenant_1_id,
        ),
    )] == [tenant_1.id, property_1.id]


def test_audit_details_persist_only_allowlisted_non_sensitive_metadata(
    identity_session: Session,
) -> None:
    graph = _create_graph(identity_session)
    service = AuditService(identity_session)

    with identity_session.begin():
        record = _append_user_role_event(
            service,
            graph,
            request_id="c1-safe-json",
            resource_id=401,
            scope_type=ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        )
        audit_id = record.id

    persisted = identity_session.get(AuditLog, audit_id)
    assert persisted is not None
    assert set(persisted.details_json or {}) == {
        "target_user_id",
        "role_code",
        "scope_type",
        "source",
    }
    serialized = json.dumps(persisted.details_json).casefold()
    for sensitive_value in (
        "eyj-fake-jwt",
        "bearer secret",
        "app-secret-value",
        "password-value",
        "sms-code-value",
        "id-card-value",
    ):
        assert sensitive_value not in serialized


def test_caller_transaction_rollback_removes_flushed_audit(
    identity_session: Session,
) -> None:
    graph = _create_graph(identity_session)
    service = AuditService(identity_session)

    with pytest.raises(RuntimeError, match="force outer rollback"):
        with identity_session.begin():
            _append_user_role_event(
                service,
                graph,
                request_id="c1-rollback",
                resource_id=501,
                scope_type=ScopeType.SYSTEM,
            )
            raise RuntimeError("force outer rollback")

    assert identity_session.query(AuditLog).count() == 0
