"""026 unproven lease/publication/transaction guards; pure, no business fixture."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.core.database import begin_owned_transaction
from app.model.task_intent import TaskIntent
from app.model.work_order import WorkOrder
from app.service.task_intent import (
    ANALYZE, INGEST, DELIVER, LEASE_KEY, IntentLeaseLost, IntentTiming,
    TaskIntentService, due, execution_limit, owns, register_intent, require_execution_lease,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def session():
    s = MagicMock(); s.info = {}; s.in_transaction.return_value = False
    s.get_transaction.return_value = s.begin.return_value.__enter__.return_value
    return s


def row(**kw):
    return NS(**{**dict(id=18, tenant_id=7, property_id=9, operation=ANALYZE,
        resource_id=22, resource_version=1, status="PENDING", task_id="task-026",
        publish_attempt=0, execution_attempt=0, lease_token=None, lease_until=None,
        available_at=NOW, last_error_code=None, finished_at=None, published_at=None,
        request_id="rt026-test"), **kw})


def service(r):
    s = TaskIntentService(session(), clock=lambda: NOW)
    s._locked = lambda _: r
    s.repo.get = lambda *a, **kw: r
    s._reconcile = lambda *a: False
    s._fail = MagicMock(side_effect=lambda r, n, c: setattr(r, "status", "FAILED"))
    return s


def test_frozen_budgets_and_time_bounds():
    assert [execution_limit(op) for op in (ANALYZE, INGEST, DELIVER)] == [3, 3, 4]
    assert IntentTiming() == IntentTiming(15, 120, 120, 20, 900)
    with pytest.raises(ValueError): execution_limit("anything")


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_terminal_never_republished_or_claimed(status):
    r = row(status=status)
    assert not due(r, NOW)
    assert service(r).claim_execution(r.id, r.task_id) is None


def test_confirmed_message_before_writeback_can_claim_and_late_writeback_cannot_regress():
    r = row(); s = service(r)
    p = s.claim_publication(r.id)
    assert r.status == "PUBLISHING" and r.publish_attempt == 1
    e = s.claim_execution(r.id, r.task_id)
    assert e["attempt"] == 1 and r.status == "PROCESSING"
    assert not s.publication_result(r.id, p["token"], sent=True)
    assert r.status == "PROCESSING"
    assert s.claim_execution(r.id, r.task_id) is None


def test_stable_task_id_and_five_publications_backoff():
    r = row(); s = service(r)
    for attempt in range(1, 6):
        r.available_at = NOW
        p = s.claim_publication(r.id)
        assert p["task_id"] == "task-026" and p["attempt"] == attempt
        assert s.publication_result(r.id, p["token"], sent=False)
        if attempt < 5:
            assert r.available_at == NOW + timedelta(seconds=2 ** (attempt - 1))
            assert s.claim_publication(r.id) is None
    assert r.status == "FAILED" and s.claim_publication(r.id) is None
    s._fail.assert_called_once()


def test_confirmed_unclaimed_deadline_recovers_not_business_success():
    r = row(); s = service(r)
    p = s.claim_publication(r.id); s.publication_result(r.id, p["token"], sent=True)
    assert r.status == "PUBLISHED" and not due(r, NOW + timedelta(seconds=119))
    assert due(r, NOW + timedelta(seconds=120))


def test_expired_execution_new_attempt_fences_old_token_without_celery_retries():
    r = row(); s = service(r)
    a = s.claim_execution(r.id, r.task_id)
    r.lease_until = NOW
    b = s.claim_execution(r.id, r.task_id)
    assert b["attempt"] == 2 and b["token"] != a["token"]
    assert not s.finish(a, "SUCCEEDED") and not s.heartbeat(a)
    assert s.finish(b, "SUCCEEDED")
    assert s.claim_execution(r.id, r.task_id) is None


def test_heartbeat_cannot_renew_past_hard_deadline_or_resurrect_expired_owner():
    r = row(); s = service(r); lease = s.claim_execution(r.id, r.task_id)
    r.available_at = NOW + timedelta(seconds=2)
    assert s.heartbeat(lease) and r.lease_until == r.available_at
    r.available_at = NOW
    assert not s.heartbeat(lease) and not owns(r, lease["token"], NOW)


def test_persistent_budget_exhaustion_once_not_multiplied_by_celery():
    r = row(); s = service(r)
    for i in range(3):
        r.available_at = NOW
        lease = s.claim_execution(r.id, r.task_id)
        assert lease["attempt"] == i + 1
        s.finish(lease, "FAILED", error="TASK.DATABASE_TRANSIENT", retryable=True)
    assert r.status == "FAILED" and r.execution_attempt == 3
    assert s.claim_execution(r.id, r.task_id) is None


def test_wrong_message_task_identity_rejected():
    r = row()
    assert service(r).claim_execution(r.id, "other-task") is None
    assert r.execution_attempt == 0


def test_register_requires_owned_transaction_and_typed_resource():
    s = session(); order = WorkOrder(id=22, tenant_id=7, property_id=9, version=1)
    with pytest.raises(Exception): register_intent(s, operation=ANALYZE, resource=order, request_id="test")
    s.in_transaction.return_value = True
    s.info.update(_unused=True)
    s.in_transaction.return_value = False
    with begin_owned_transaction(s):
        s.in_transaction.return_value = True
        with pytest.raises(ValueError): register_intent(s, operation=INGEST, resource=order, request_id="test")
        with pytest.raises(ValueError): register_intent(s, operation=ANALYZE, resource=order, request_id="test", scope=(8, 9))


def test_model_ddl_has_exact_approved_constraints_not_payload_or_contact():
    ddl = str(CreateTable(TaskIntent.__table__).compile(dialect=mysql.dialect()))
    for token in ("resource_generation", "lease_consistent", "attempt_nonnegative", "ascii_bin", "FOREIGN KEY(property_id, tenant_id)"):
        assert token in ddl
    assert "contact_phone" not in ddl and "payload" not in ddl
    assert len(TaskIntent.__table__.columns) == 20


@pytest.mark.parametrize("url,env", [
    ("mysql+pymysql://u:p@localhost:3307/property_ai_auth001_test", "test"),
    ("mysql+pymysql://u:p@127.0.0.1:3307/property_ai_test", "test"),
    ("mysql+pymysql://u:p@127.0.0.1:3306/property_ai_auth001_test", "test"),
    ("mysql+pymysql://u:p@127.0.0.1:3307/property_ai_auth001_test", "production"),
])
def test_migration_wrong_target_before_any_sql(monkeypatch, url, env):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/20260925_0012_task_intents.py"
    spec = importlib.util.spec_from_file_location("migration026", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    bind = MagicMock(); bind.engine.url = make_url(url)
    monkeypatch.setenv("APP_ENV", env); monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    with pytest.raises(RuntimeError): module.upgrade()
    bind.execute.assert_not_called()


def test_test_publisher_cannot_scan_all_intents():
    from app.worker.task_intents import publish_due
    with pytest.raises(ValueError):
        publish_due(MagicMock(), Settings(_env_file=None, app_env="test", durable_tasks_enabled=True))


def test_deployment_publisher_requires_durable_mode_before_engine_creation(monkeypatch):
    from app.worker import task_intent_publisher as publisher
    create = MagicMock();monkeypatch.setattr(publisher,"create_database_engine",create)
    with pytest.raises(RuntimeError,match="DURABLE_TASKS_ENABLED"):
        publisher.serve(seconds=30,settings=Settings(_env_file=None,app_env="staging",durable_tasks_enabled=False))
    create.assert_not_called()


def test_deployment_publisher_scans_only_intents_and_disposes_engine(monkeypatch):
    from app.worker import task_intent_publisher as publisher
    engine=MagicMock();run=MagicMock()
    monkeypatch.setattr(publisher,"create_database_engine",lambda _url:engine)
    monkeypatch.setattr(publisher,"run_publisher",run)
    settings=Settings(_env_file=None,app_env="staging",durable_tasks_enabled=True,
        database_url="mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check")
    publisher.serve(seconds=30,settings=settings)
    assert run.call_args.kwargs["ids_supplier"]() is None
    assert run.call_args.kwargs["seconds"]==30
    engine.dispose.assert_called_once_with()


def test_committed_delivery_is_reconciled_even_when_all_attempts_spent():
    r = row(operation=DELIVER, execution_attempt=4, publish_attempt=5)
    s = service(r)
    s._reconcile = TaskIntentService._reconcile.__get__(s)
    s.session.get.return_value = NS(status="SUCCEEDED")
    assert s.claim_publication(r.id) is None
    assert r.status == "SUCCEEDED" and r.execution_attempt == 4
    s._fail.assert_not_called()


def test_retry_schema_cannot_supply_task_scope_or_arbitrary_reason():
    from app.api.v1.task_intents import RetryIntentRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError): RetryIntentRequest(reason_code='free text with private info')
    with pytest.raises(ValidationError): RetryIntentRequest(reason_code='OPERATOR_VERIFIED',tenant_id=8)


@pytest.mark.parametrize('operation',[ANALYZE,INGEST,DELIVER])
def test_manager_scope_not_composed_from_unrelated_tenant_grants(monkeypatch, operation):
    from app.domain.iam import Principal, RoleGrant, OrganizationScope, ScopeType
    from app.service.auth import AuthService
    from app.core.errors import AppError
    permission={ANALYZE:'ai_analysis:review',INGEST:'knowledge:manage',DELIVER:'property:manage'}[operation]
    actor=Principal(4,'ACTIVE','test',(
        RoleGrant('TENANT_ADMIN',OrganizationScope(ScopeType.TENANT,tenant_id=8),frozenset({permission})),
        RoleGrant('RESIDENT',OrganizationScope(ScopeType.PROPERTY,tenant_id=7,property_id=9),frozenset()),))
    monkeypatch.setattr(AuthService,'load_principal',lambda *a:actor)
    with pytest.raises(AppError) as error: service(row())._authorize_manager(row(operation=operation),actor)
    assert error.value.status_code==404


def test_retry_preserves_failure_and_creates_only_one_generation_with_audit(monkeypatch):
    from app.service.audit import AuditService
    from app.repository.work_order import WorkOrderRepository
    from app.domain.iam import OrganizationScope,ScopeType
    r=row(status='FAILED',generation=1,execution_attempt=3,publish_attempt=3)
    s=service(r);s.session.in_transaction.return_value=False
    s._authorize_manager=lambda *a:(NS(user_id=4),OrganizationScope(ScopeType.PROPERTY,tenant_id=7,property_id=9))
    s.session.scalar.return_value=None
    monkeypatch.setattr(WorkOrderRepository,'get_work_order_for_update',lambda *a:NS(version=1,status='PENDING_ANALYSIS',current_assignment_id=None,id=r.resource_id))
    audit=MagicMock();monkeypatch.setattr(AuditService,'append',audit)
    added=[]
    def add(new):new.id=21;added.append(new);return new
    s.repo.add=add
    result=s.retry_managed(r.id,actor=NS(user_id=4),reason_code='DEPENDENCY_RECOVERED',request_id='026-recovery')
    assert result['generation']==2 and result['execution_attempt']==0 and result['status']=='PENDING'
    assert r.status=='FAILED' and r.execution_attempt==3 and r.publish_attempt==3
    audit.assert_called_once()
    s.session.scalar.return_value=added[0]
    again=s.retry_managed(r.id,actor=NS(user_id=4),reason_code='DEPENDENCY_RECOVERED',request_id='026-recovery-duplicate')
    assert again==result and len(added)==1 and audit.call_count==1
    assert not any('token' in key or 'phone' in key for key in result)


def test_recovery_rejects_new_business_version_and_nonfailed_state(monkeypatch):
    from app.repository.work_order import WorkOrderRepository
    from app.core.errors import AppError
    r=row(status='FAILED',generation=1);s=service(r)
    s._authorize_manager=lambda *a:(NS(user_id=4),None);s.session.scalar.return_value=None
    monkeypatch.setattr(WorkOrderRepository,'get_work_order_for_update',lambda *a:NS(version=8,status='PENDING_ANALYSIS',current_assignment_id=None,id=r.resource_id))
    with pytest.raises(AppError) as error:s.retry_managed(r.id,actor=None,reason_code='OPERATOR_VERIFIED',request_id='026')
    assert error.value.status_code==409
    r.status='SUCCEEDED'
    with pytest.raises(AppError):s.retry_managed(r.id,actor=None,reason_code='OPERATOR_VERIFIED',request_id='026')


def test_recovery_audit_uses_existing_validated_append_only_contract():
    from app.service.audit import AuditService
    from app.domain.audit import AuditAction,AuditResourceType
    from app.domain.iam import ScopeType
    repo=MagicMock()
    AuditService(session(),repository=repo).append(action=AuditAction.TASK_INTENT_RETRIED,
        resource_type=AuditResourceType.TASK_INTENT,resource_id=9,actor_user_id=4,
        scope_type=ScopeType.PROPERTY,tenant_id=7,property_id=9,request_id='026',
        details={'previous_id':8,'generation':2,'reason_code':'DEPENDENCY_RECOVERED'})
    repo.append.assert_called_once()


@pytest.mark.parametrize('operation,attempt',[(DELIVER,4),(INGEST,3)])
def test_final_commit_ack_failure_reconciles_committed_domain_in_finish(operation,attempt):
    r=row(operation=operation,status='PROCESSING',execution_attempt=attempt,
          lease_token='owner',lease_until=NOW+timedelta(seconds=120),available_at=NOW+timedelta(seconds=900))
    s=service(r);s._reconcile=TaskIntentService._reconcile.__get__(s)
    s.session.get.return_value=NS(status='SUCCEEDED')
    assert s.finish({'id':r.id,'token':'owner'},'FAILED',error='TASK.DATABASE_TRANSIENT',retryable=True)
    assert r.status=='SUCCEEDED' and r.execution_attempt==attempt
    s._fail.assert_not_called()


def test_final_analysis_commit_ack_failure_keeps_single_committed_assignment(monkeypatch):
    from app.repository.work_order import WorkOrderRepository
    from app.repository.ai import AiRepository
    r=row(status='PROCESSING',execution_attempt=3,lease_token='owner',
          lease_until=NOW+timedelta(seconds=120),available_at=NOW+timedelta(seconds=900))
    s=service(r);s._reconcile=TaskIntentService._reconcile.__get__(s)
    work=NS(id=22,status='ASSIGNED',version=3,current_assignment_id=42)
    claim=dict(task_id=r.task_id,analysis_id=5,input_hash='abc',work_order_version=1,from_status='PENDING_ANALYSIS')
    monkeypatch.setattr(WorkOrderRepository,'get_work_order_for_update',lambda *a:work)
    monkeypatch.setattr(WorkOrderRepository,'latest_analysis_attempt',lambda *a:NS(payload_json=claim))
    monkeypatch.setattr(AiRepository,'get_current_analysis_for_update',lambda *a:NS(id=5,input_hash='abc',status='COMPLETED'))
    assert s.finish({'id':r.id,'token':'owner'},'FAILED',error='TASK.DATABASE_TRANSIENT',retryable=True)
    assert r.status=='SUCCEEDED' and work.current_assignment_id==42 and work.version==3
    s._fail.assert_not_called()


@pytest.mark.parametrize('operation,attempt',[(DELIVER,4),(INGEST,3)])
def test_real_uncommitted_final_failure_remains_terminal_not_false_success(operation,attempt):
    r=row(operation=operation,status='PROCESSING',execution_attempt=attempt,lease_token='owner',
          lease_until=NOW+timedelta(seconds=120),available_at=NOW+timedelta(seconds=900))
    s=service(r);s._reconcile=TaskIntentService._reconcile.__get__(s)
    s.session.get.return_value=NS(status='FAILED')
    assert s.finish({'id':r.id,'token':'owner'},'FAILED',error='TASK.DATABASE_TRANSIENT',retryable=True)
    assert r.status=='FAILED' and r.execution_attempt==attempt
    s._fail.assert_called_once()


def test_cancelled_work_is_not_executed_after_a_lost_process(monkeypatch):
    r = row(execution_attempt=1)
    s = service(r)
    s._reconcile = TaskIntentService._reconcile.__get__(s)
    from app.repository.work_order import WorkOrderRepository
    monkeypatch.setattr(WorkOrderRepository, 'get_work_order_for_update', lambda *a: NS(status="CANCELLED"))
    assert s.claim_execution(r.id, r.task_id) is None
    assert r.status == "CANCELLED" and r.execution_attempt == 1


def test_last_attempt_committed_low_confidence_is_not_mislabeled_exhausted(monkeypatch):
    from app.repository.work_order import WorkOrderRepository
    from app.repository.ai import AiRepository
    r = row(execution_attempt=3, publish_attempt=5)
    s = service(r); s._reconcile = TaskIntentService._reconcile.__get__(s)
    work = NS(id=22, status='PENDING_ANALYSIS', version=1, current_assignment_id=None)
    claim = dict(task_id=r.task_id, analysis_id=5, input_hash='abc', work_order_version=1, from_status='PENDING_ANALYSIS')
    monkeypatch.setattr(WorkOrderRepository, 'get_work_order_for_update', lambda *a: work)
    monkeypatch.setattr(WorkOrderRepository, 'latest_analysis_attempt', lambda *a: NS(payload_json=claim))
    monkeypatch.setattr(AiRepository, 'get_current_analysis_for_update', lambda *a: NS(id=5,input_hash='abc',status='LOW_CONFIDENCE'))
    assert s.claim_publication(r.id) is None
    assert r.status == 'SUCCEEDED' and r.execution_attempt == 3
    s._fail.assert_not_called()
