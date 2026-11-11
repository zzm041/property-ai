"""Audited RBAC mutation transactions on disposable MySQL 8."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_session_factory
from app.core.errors import AppError, IamErrorCode
from app.domain.governance import AuditAction
from app.domain.identity import OrganizationScope, ScopeType
from app.model.identity import AuditLog, RolePermission, UserRole
from app.repository.rbac_mutation import RbacMutationRepository
from app.service.audit import AuditService
from app.service.identity.rbac_mutation import RbacMutationService
from tests.integration.rbac_mutation_test_data import (
    RbacMutationGraph,
    create_rbac_mutation_graph,
    identity_only_principal,
)


class FailingAuditService:
    def append(self, **values: object) -> None:
        del values
        raise RuntimeError("forced audit failure")


class GrantBarrierRepository(RbacMutationRepository):
    def __init__(self, session: Session, barrier: Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    def find_user_role(
        self,
        *,
        user_id: int,
        role_id: int,
        scope: OrganizationScope,
    ) -> UserRole | None:
        result = super().find_user_role(
            user_id=user_id,
            role_id=role_id,
            scope=scope,
        )
        self._barrier.wait(timeout=10)
        return result


class RevokeBarrierRepository(RbacMutationRepository):
    def __init__(self, session: Session, barrier: Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    def find_user_role_for_update(
        self,
        *,
        user_id: int,
        role_id: int,
        scope: OrganizationScope,
    ) -> UserRole | None:
        self._barrier.wait(timeout=10)
        return super().find_user_role_for_update(
            user_id=user_id,
            role_id=role_id,
            scope=scope,
        )


def _service(session: Session) -> RbacMutationService:
    return RbacMutationService(session)


def _count_target_user_roles(
    session: Session,
    graph: RbacMutationGraph,
) -> int:
    return session.scalar(
        select(func.count())
        .select_from(UserRole)
        .where(
            UserRole.user_id == graph.target_user_id,
            UserRole.role_id == graph.target_role_id,
        )
    )


def test_system_tenant_property_grants_and_audits_are_atomic(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    service = _service(identity_session)

    system_result = service.grant_user_role(
        actor=identity_only_principal(graph.system_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=OrganizationScope(ScopeType.SYSTEM),
        request_id="c2-system-grant",
    )
    tenant_result = service.grant_user_role(
        actor=identity_only_principal(graph.tenant_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=OrganizationScope(
            ScopeType.TENANT,
            tenant_id=graph.tenant_1_id,
        ),
        request_id="c2-tenant-grant",
    )
    property_result = service.grant_user_role(
        actor=identity_only_principal(graph.property_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=OrganizationScope(
            ScopeType.PROPERTY,
            tenant_id=graph.tenant_1_id,
            property_id=graph.property_1_id,
        ),
        request_id="c2-property-grant",
    )

    relation_ids = {
        system_result.relation_id,
        tenant_result.relation_id,
        property_result.relation_id,
    }
    relations = identity_session.scalars(
        select(UserRole)
        .where(UserRole.id.in_(relation_ids))
        .order_by(UserRole.id)
    ).all()
    assert len(relations) == 3
    assert {relation.role_id for relation in relations} == {
        graph.target_role_id
    }
    assert {
        (
            relation.scope_type,
            relation.tenant_id,
            relation.property_id,
        )
        for relation in relations
    } == {
        ("SYSTEM", None, None),
        ("TENANT", graph.tenant_1_id, None),
        (
            "PROPERTY",
            graph.tenant_1_id,
            graph.property_1_id,
        ),
    }
    audits = identity_session.scalars(
        select(AuditLog)
        .where(AuditLog.resource_id.in_(relation_ids))
        .order_by(AuditLog.id)
    ).all()
    assert len(audits) == 3
    assert {audit.action for audit in audits} == {
        AuditAction.IAM_USER_ROLE_GRANTED.value
    }
    assert {audit.details_json["source"] for audit in audits} == {"INTERNAL"}
    assert {audit.request_id for audit in audits} == {
        "c2-system-grant",
        "c2-tenant-grant",
        "c2-property-grant",
    }


def test_invalid_and_mismatched_scope_are_rejected_without_mutation(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    service = _service(identity_session)

    with pytest.raises(AppError) as invalid:
        service.grant_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=cast(OrganizationScope, object()),
            request_id="c2-invalid-scope",
        )
    with pytest.raises(AppError) as mismatched:
        service.grant_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_3_id,
            ),
            request_id="c2-mismatched-scope",
        )

    assert invalid.value.code == IamErrorCode.INVALID_SCOPE
    assert mismatched.value.code == IamErrorCode.INVALID_SCOPE
    assert _count_target_user_roles(identity_session, graph) == 0
    assert identity_session.scalar(
        select(func.count()).select_from(AuditLog)
    ) == 0


def test_duplicate_and_wrong_scope_revoke_have_stable_behavior(
    identity_session: Session,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    service = _service(identity_session)
    property_1_scope = OrganizationScope(
        ScopeType.PROPERTY,
        tenant_id=graph.tenant_1_id,
        property_id=graph.property_1_id,
    )
    result = service.grant_user_role(
        actor=identity_only_principal(graph.system_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=property_1_scope,
        request_id="c2-first-grant",
    )

    with pytest.raises(AppError) as duplicate:
        service.grant_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=property_1_scope,
            request_id="c2-duplicate-grant",
        )
    with pytest.raises(AppError) as wrong_scope:
        service.revoke_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=OrganizationScope(
                ScopeType.PROPERTY,
                tenant_id=graph.tenant_1_id,
                property_id=graph.property_2_id,
            ),
            request_id="c2-wrong-scope-revoke",
        )

    assert duplicate.value.code == IamErrorCode.DUPLICATE_GRANT
    assert "1062" not in duplicate.value.message
    assert wrong_scope.value.code == IamErrorCode.GRANT_NOT_FOUND

    revoke = service.revoke_user_role(
        actor=identity_only_principal(graph.system_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=property_1_scope,
        request_id="c2-correct-revoke",
    )
    with pytest.raises(AppError) as missing:
        service.revoke_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=property_1_scope,
            request_id="c2-second-revoke",
        )

    assert revoke.relation_id == result.relation_id
    assert missing.value.code == IamErrorCode.GRANT_NOT_FOUND
    revoke_audits = identity_session.scalars(
        select(AuditLog).where(
            AuditLog.action == AuditAction.IAM_USER_ROLE_REVOKED.value,
            AuditLog.resource_id == result.relation_id,
        )
    ).all()
    assert len(revoke_audits) == 1
    assert revoke_audits[0].request_id == "c2-correct-revoke"


def test_role_permission_uses_nonsequential_real_ids_and_system_audit(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    service = _service(identity_session)

    grant = service.grant_role_permission(
        actor=identity_only_principal(graph.system_actor_id),
        role_code="STAFF",
        permission_code="property:read",
        request_id="c2-role-permission-grant",
    )
    with get_session_factory(integration_settings.test_database_url)() as verify:
        relation = verify.get(RolePermission, grant.relation_id)
        assert relation is not None
        assert relation.role_id == graph.target_role_id
        assert relation.permission_id == graph.target_permission_id
    assert graph.target_role_id != graph.target_permission_id
    assert graph.target_role_id > 1
    assert graph.target_permission_id > 1

    with pytest.raises(AppError) as duplicate:
        service.grant_role_permission(
            actor=identity_only_principal(graph.system_actor_id),
            role_code="STAFF",
            permission_code="property:read",
            request_id="c2-role-permission-duplicate",
        )
    assert duplicate.value.code == IamErrorCode.DUPLICATE_GRANT

    revoke = service.revoke_role_permission(
        actor=identity_only_principal(graph.system_actor_id),
        role_code="STAFF",
        permission_code="property:read",
        request_id="c2-role-permission-revoke",
    )
    with pytest.raises(AppError) as missing:
        service.revoke_role_permission(
            actor=identity_only_principal(graph.system_actor_id),
            role_code="STAFF",
            permission_code="property:read",
            request_id="c2-role-permission-missing",
        )

    assert revoke.relation_id == grant.relation_id
    assert missing.value.code == IamErrorCode.GRANT_NOT_FOUND
    audits = identity_session.scalars(
        select(AuditLog)
        .where(AuditLog.resource_id == grant.relation_id)
        .order_by(AuditLog.id)
    ).all()
    assert [audit.action for audit in audits] == [
        AuditAction.IAM_ROLE_PERMISSION_GRANTED.value,
        AuditAction.IAM_ROLE_PERMISSION_REVOKED.value,
    ]
    assert all(
        audit.tenant_id is None and audit.property_id is None
        for audit in audits
    )


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_user_role_audit_failure_rolls_back_relation(
    identity_session: Session,
    integration_settings: Settings,
    operation: str,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    scope = OrganizationScope(ScopeType.SYSTEM)
    normal = _service(identity_session)
    existing_id: int | None = None
    if operation == "revoke":
        existing_id = normal.grant_user_role(
            actor=identity_only_principal(graph.system_actor_id),
            target_user_id=graph.target_user_id,
            role_code="STAFF",
            scope=scope,
            request_id="c2-setup-grant",
        ).relation_id

    failing = RbacMutationService(
        identity_session,
        audit_service=cast(AuditService, FailingAuditService()),
    )
    with pytest.raises(RuntimeError, match="forced audit failure"):
        if operation == "grant":
            failing.grant_user_role(
                actor=identity_only_principal(graph.system_actor_id),
                target_user_id=graph.target_user_id,
                role_code="STAFF",
                scope=scope,
                request_id="c2-failing-grant",
            )
        else:
            failing.revoke_user_role(
                actor=identity_only_principal(graph.system_actor_id),
                target_user_id=graph.target_user_id,
                role_code="STAFF",
                scope=scope,
                request_id="c2-failing-revoke",
            )

    with get_session_factory(integration_settings.test_database_url)() as verify:
        rows = verify.scalars(
            select(UserRole).where(
                UserRole.user_id == graph.target_user_id,
                UserRole.role_id == graph.target_role_id,
                UserRole.scope_type == "SYSTEM",
            )
        ).all()
        assert len(rows) == (0 if operation == "grant" else 1)
        if existing_id is not None:
            assert rows[0].id == existing_id
        assert verify.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.request_id.like("c2-failing-%"))
        ) == 0


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_role_permission_audit_failure_rolls_back_relation(
    identity_session: Session,
    integration_settings: Settings,
    operation: str,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    normal = _service(identity_session)
    existing_id: int | None = None
    if operation == "revoke":
        existing_id = normal.grant_role_permission(
            actor=identity_only_principal(graph.system_actor_id),
            role_code="STAFF",
            permission_code="property:read",
            request_id="c2-setup-role-permission",
        ).relation_id

    failing = RbacMutationService(
        identity_session,
        audit_service=cast(AuditService, FailingAuditService()),
    )
    with pytest.raises(RuntimeError, match="forced audit failure"):
        if operation == "grant":
            failing.grant_role_permission(
                actor=identity_only_principal(graph.system_actor_id),
                role_code="STAFF",
                permission_code="property:read",
                request_id="c2-failing-role-permission-grant",
            )
        else:
            failing.revoke_role_permission(
                actor=identity_only_principal(graph.system_actor_id),
                role_code="STAFF",
                permission_code="property:read",
                request_id="c2-failing-role-permission-revoke",
            )

    with get_session_factory(integration_settings.test_database_url)() as verify:
        rows = verify.scalars(
            select(RolePermission).where(
                RolePermission.role_id == graph.target_role_id,
                RolePermission.permission_id == graph.target_permission_id,
            )
        ).all()
        assert len(rows) == (0 if operation == "grant" else 1)
        if existing_id is not None:
            assert rows[0].id == existing_id


def test_concurrent_user_role_grant_has_one_success_and_one_duplicate(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    barrier = Barrier(2)
    factory = get_session_factory(integration_settings.test_database_url)

    def worker() -> str:
        with factory() as session:
            service = RbacMutationService(
                session,
                repository=GrantBarrierRepository(session, barrier),
            )
            try:
                service.grant_user_role(
                    actor=identity_only_principal(graph.system_actor_id),
                    target_user_id=graph.target_user_id,
                    role_code="STAFF",
                    scope=OrganizationScope(ScopeType.SYSTEM),
                    request_id="c2-concurrent-grant",
                )
            except AppError as exc:
                return exc.code
            return "SUCCESS"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(worker), executor.submit(worker))
        outcomes = sorted(future.result() for future in futures)

    assert outcomes == [IamErrorCode.DUPLICATE_GRANT, "SUCCESS"]
    with factory() as verify:
        assert _count_target_user_roles(verify, graph) == 1
        assert verify.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == AuditAction.IAM_USER_ROLE_GRANTED.value,
                AuditLog.request_id == "c2-concurrent-grant",
            )
        ) == 1


def test_concurrent_revoke_has_one_success_and_one_not_found(
    identity_session: Session,
    integration_settings: Settings,
) -> None:
    graph = create_rbac_mutation_graph(identity_session)
    _service(identity_session).grant_user_role(
        actor=identity_only_principal(graph.system_actor_id),
        target_user_id=graph.target_user_id,
        role_code="STAFF",
        scope=OrganizationScope(ScopeType.SYSTEM),
        request_id="c2-concurrent-revoke-setup",
    )
    barrier = Barrier(2)
    factory = get_session_factory(integration_settings.test_database_url)

    def worker() -> str:
        with factory() as session:
            service = RbacMutationService(
                session,
                repository=RevokeBarrierRepository(session, barrier),
            )
            try:
                service.revoke_user_role(
                    actor=identity_only_principal(graph.system_actor_id),
                    target_user_id=graph.target_user_id,
                    role_code="STAFF",
                    scope=OrganizationScope(ScopeType.SYSTEM),
                    request_id="c2-concurrent-revoke",
                )
            except AppError as exc:
                return exc.code
            return "SUCCESS"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(worker), executor.submit(worker))
        outcomes = sorted(future.result() for future in futures)

    assert outcomes == [IamErrorCode.GRANT_NOT_FOUND, "SUCCESS"]
    with factory() as verify:
        assert _count_target_user_roles(verify, graph) == 0
        assert verify.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == AuditAction.IAM_USER_ROLE_REVOKED.value,
                AuditLog.request_id == "c2-concurrent-revoke",
            )
        ) == 1
