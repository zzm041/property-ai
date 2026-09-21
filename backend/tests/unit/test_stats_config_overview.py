"""Scoped current-operations overview authorization, aggregation, and page wiring."""

from datetime import UTC, date, datetime
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mysql

from app.api.dependencies.auth import get_current_principal
from app.api.v1.admin_operations import get_operations_service, router
from app.core.errors import AppError, IamErrorCode, StatsErrorCode
from app.domain.dev_seed import SEED_ROLE_PERMISSIONS
from app.domain.iam import OrganizationScope, Principal, RoleGrant, ScopeType
from app.domain.operations import STATS_READ
from app.domain.work_order import WorkOrderStatus
from app.repository.operations import OperationsRepository
from app.schema.operations import (
    IncidentStatsData,
    OperationsOverviewData,
    SlaClockStatsData,
    StaffingStatsData,
    WorkOrderStatsData,
)
from app.service.operations import OperationsService


NOW = datetime(2026, 9, 21, 16, 30, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _grant(
    role_code: str,
    scope_type: ScopeType,
    *,
    tenant_id: int = 1,
    property_id: int | None = None,
    permissions: tuple[str, ...] = (STATS_READ,),
) -> RoleGrant:
    return RoleGrant(
        role_code=role_code,
        scope=OrganizationScope(
            scope_type,
            tenant_id=tenant_id,
            property_id=property_id,
        ),
        permissions=frozenset(permissions),
    )


def _actor(*grants: RoleGrant) -> Principal:
    return Principal(
        user_id=41,
        status="ACTIVE",
        display_name="Synthetic operations manager",
        grants=tuple(grants),
    )


def _property(property_id: int, tenant_id: int = 1, timezone: str = "Asia/Shanghai"):
    return SimpleNamespace(
        id=property_id,
        tenant_id=tenant_id,
        name=f"Synthetic property {property_id}",
        timezone=timezone,
    )


def _service() -> OperationsService:
    service = OperationsService.__new__(OperationsService)
    service._repository = MagicMock()
    service._properties = MagicMock()
    service._clock = lambda: NOW
    service._properties.get_tenant.return_value = SimpleNamespace(id=1, status="ACTIVE")
    service._properties.get_property_by_id.return_value = _property(10)
    service._properties.list_all_properties.return_value = (_property(10),)
    service._repository.list_work_order_status_counts.return_value = ()
    service._repository.list_work_order_category_counts.return_value = ()
    service._repository.list_work_order_priority_counts.return_value = ()
    service._repository.list_incident_status_counts.return_value = ()
    service._repository.list_sla_clock_state_counts.return_value = ()
    service._repository.list_active_staff_assignment_counts.return_value = ()
    service._repository.list_current_staffing.return_value = ()
    return service


def test_seed_stats_permission_is_limited_to_property_and_tenant_admins() -> None:
    assert STATS_READ in SEED_ROLE_PERMISSIONS["PROPERTY_ADMIN"]
    assert STATS_READ in SEED_ROLE_PERMISSIONS["TENANT_ADMIN"]
    for role in ("SYSTEM_ADMIN", "SUPERVISOR", "STAFF", "RESIDENT"):
        assert STATS_READ not in SEED_ROLE_PERMISSIONS[role]


def test_property_overview_zero_fills_and_uses_frozen_status_semantics() -> None:
    actor = _actor(
        _grant("PROPERTY_ADMIN", ScopeType.PROPERTY, property_id=10)
    )
    service = _service()
    service._repository.list_work_order_status_counts.return_value = (
        (10, "PENDING_ASSIGNMENT", 2),
        (10, "COMPLETED", 3),
        (10, "CANCELLED", 1),
        (10, "CLOSED", 4),
    )
    service._repository.list_work_order_category_counts.return_value = (
        (10, "PLUMBING", 10),
    )
    service._repository.list_work_order_priority_counts.return_value = (
        (10, "MEDIUM", 10),
    )
    service._repository.list_incident_status_counts.return_value = (
        (10, "OPEN", 2),
        (10, "MONITORING", 1),
        (10, "CLOSED", 5),
    )
    service._repository.list_sla_clock_state_counts.return_value = (
        (10, "WARNING", 2),
        (10, "OVERDUE", 1),
    )
    service._repository.list_active_staff_assignment_counts.return_value = ((10, 4),)
    service._repository.list_current_staffing.return_value = ((10, 3, 2, 1, 2, 5),)

    result = service.get_overview(
        actor=actor,
        scope_type="PROPERTY",
        tenant_id=1,
        property_id=10,
    )

    assert result.work_orders.total == 10
    assert result.work_orders.open == 2
    assert result.work_orders.terminal == 8
    assert len(result.work_orders.by_status) == len(WorkOrderStatus)
    status_counts = {item.code: item.count for item in result.work_orders.by_status}
    assert status_counts["PENDING_ANALYSIS"] == 0
    assert result.incidents.total == 8
    assert result.incidents.active == 3
    assert result.sla_clocks.warning == 2
    assert result.sla_clocks.overdue == 1
    assert result.staffing.active_property_assignments == 4
    assert result.staffing.current_status_records == 3
    assert result.staffing.unreported_status == 1
    assert result.staffing.on_duty == 2
    assert result.properties[0].business_date == date(2026, 9, 22)
    assert result.properties[0].unreported_status == 1
    staffing_call = service._repository.list_current_staffing.call_args.kwargs
    assignment_call = (
        service._repository.list_active_staff_assignment_counts.call_args.kwargs
    )
    assert staffing_call["at"] is NOW
    assert assignment_call["at"] is NOW


def test_tenant_overview_uses_one_instant_for_each_property_timezone() -> None:
    actor = _actor(_grant("TENANT_ADMIN", ScopeType.TENANT))
    service = _service()
    service._properties.list_all_properties.return_value = (
        _property(10, timezone="Asia/Shanghai"),
        _property(20, timezone="America/New_York"),
    )

    result = service.get_overview(
        actor=actor,
        scope_type="TENANT",
        tenant_id=1,
        property_id=None,
    )

    assert result.property_count == 2
    assert [item.business_date for item in result.properties] == [
        date(2026, 9, 22),
        date(2026, 9, 21),
    ]
    assert service._repository.list_current_staffing.call_args.kwargs[
        "business_dates"
    ] == {
        10: date(2026, 9, 22),
        20: date(2026, 9, 21),
    }
    for method_name in (
        "list_work_order_status_counts",
        "list_work_order_category_counts",
        "list_work_order_priority_counts",
        "list_incident_status_counts",
        "list_sla_clock_state_counts",
        "list_active_staff_assignment_counts",
        "list_current_staffing",
    ):
        assert getattr(service._repository, method_name).call_count == 1


def test_mixed_grants_cannot_authorize_another_tenant_property() -> None:
    actor = _actor(
        _grant("PROPERTY_ADMIN", ScopeType.PROPERTY, tenant_id=1, property_id=10),
        _grant("TENANT_ADMIN", ScopeType.TENANT, tenant_id=2),
    )
    service = _service()
    service._properties.get_property_by_id.return_value = _property(20, tenant_id=2)

    with pytest.raises(AppError) as captured:
        service.get_overview(
            actor=actor,
            scope_type="PROPERTY",
            tenant_id=1,
            property_id=20,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._properties.list_all_properties.assert_not_called()
    service._repository.list_work_order_status_counts.assert_not_called()


def test_property_admin_cannot_escalate_property_grant_to_tenant_summary() -> None:
    actor = _actor(
        _grant("PROPERTY_ADMIN", ScopeType.PROPERTY, property_id=10)
    )
    service = _service()

    with pytest.raises(AppError) as captured:
        service.get_overview(
            actor=actor,
            scope_type="TENANT",
            tenant_id=1,
            property_id=None,
        )

    assert captured.value.code == IamErrorCode.FORBIDDEN
    service._properties.list_all_properties.assert_not_called()
    service._repository.list_work_order_status_counts.assert_not_called()


def test_tenant_scope_rejects_client_property_parameter() -> None:
    actor = _actor(_grant("TENANT_ADMIN", ScopeType.TENANT))
    service = _service()

    with pytest.raises(AppError) as captured:
        service.get_overview(
            actor=actor,
            scope_type="TENANT",
            tenant_id=1,
            property_id=10,
        )

    assert captured.value.code == StatsErrorCode.INVALID_INPUT
    service._repository.list_work_order_status_counts.assert_not_called()


def test_repository_queries_bind_scope_and_current_employment_window() -> None:
    session = MagicMock()
    session.execute.return_value.all.return_value = ()
    repository = OperationsRepository(session)

    repository.list_work_order_status_counts(tenant_id=1, property_ids=(10, 20))
    work_order_sql = str(
        session.execute.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "work_orders.tenant_id = %s" in work_order_sql
    assert "work_orders.property_id IN" in work_order_sql

    repository.list_active_staff_assignment_counts(
        tenant_id=1,
        property_ids=(10, 20),
        at=NOW,
    )
    assignment_sql = str(
        session.execute.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "employee_properties.tenant_id = %s" in assignment_sql
    assert "employee_properties.valid_from <= %s" in assignment_sql
    assert "employee_properties.valid_to IS NULL" in assignment_sql
    assert "employee_properties.valid_to > %s" in assignment_sql

    repository.list_current_staffing(
        tenant_id=1,
        business_dates={10: date(2026, 9, 22), 20: date(2026, 9, 21)},
        at=NOW,
    )
    staffing_sql = str(
        session.execute.call_args.args[0].compile(dialect=mysql.dialect())
    )
    assert "JOIN employee_properties" in staffing_sql
    assert "employee_daily_statuses.tenant_id = %s" in staffing_sql
    assert "employee_daily_statuses.business_date = %s" in staffing_sql
    assert "employee_properties.valid_from <= %s" in staffing_sql


def test_normal_query_string_ids_are_parsed_as_integers() -> None:
    app = FastAPI()
    app.include_router(router)
    actor = _actor(
        _grant("PROPERTY_ADMIN", ScopeType.PROPERTY, property_id=10)
    )
    result = OperationsOverviewData(
        scope_type="PROPERTY",
        tenant_id=1,
        property_id=10,
        generated_at=NOW,
        property_count=0,
        work_orders=WorkOrderStatsData(
            total=0,
            open=0,
            terminal=0,
            by_status=[],
            by_category=[],
            by_priority=[],
        ),
        incidents=IncidentStatsData(total=0, active=0, by_status=[]),
        sla_clocks=SlaClockStatsData(warning=0, overdue=0, by_state=[]),
        staffing=StaffingStatsData(
            active_property_assignments=0,
            current_status_records=0,
            unreported_status=0,
            on_duty=0,
            available=0,
            current_load=0,
            on_duty_capacity=0,
        ),
        properties=[],
    )
    service = MagicMock()
    service.get_overview.return_value = result
    app.dependency_overrides[get_current_principal] = lambda: actor
    app.dependency_overrides[get_operations_service] = lambda: service

    with TestClient(app) as client:
        response = client.get(
            "/admin/operations-overview"
            "?scope_type=PROPERTY&tenant_id=1&property_id=10"
        )

    assert response.status_code == 200
    call = service.get_overview.call_args.kwargs
    assert call["tenant_id"] == 1
    assert call["property_id"] == 10


def test_miniprogram_exposes_only_scoped_snapshot_and_existing_config_links() -> None:
    page = (
        REPO_ROOT / "miniprogram/pages/supervisor/dashboard.js"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "miniprogram/pages/supervisor/dashboard.wxml"
    ).read_text(encoding="utf-8")
    api = (REPO_ROOT / "miniprogram/services/api.js").read_text(encoding="utf-8")
    home = (REPO_ROOT / "miniprogram/pages/home/home.js").read_text(
        encoding="utf-8"
    )
    app_json = (REPO_ROOT / "miniprogram/app.json").read_text(encoding="utf-8")

    assert 'indexOf("stats:read")' in page
    assert 'scope.scope_type === "TENANT"' in page
    assert "scopeAllows(scope, entry.permission" in page
    assert "当前状态快照，不是今日新增或期间绩效报表" in template
    assert "事件总数" in template
    assert 'const SLA_LABELS = { OPEN: "计时中" }' in page
    assert "不会在本页保存另一套配置" in template
    assert "/pages/supervisor/staff" in page
    assert "/pages/supervisor/sla" in page
    assert "/pages/supervisor/properties" in page
    assert "/pages/supervisor/knowledge" in page
    assert "provider" not in page.lower()
    assert "secret" not in page.lower()
    assert "/admin/operations-overview" in api
    assert 'canViewStats: permissions.indexOf("stats:read") >= 0' in home
    assert '"pages/supervisor/dashboard"' in app_json


def test_miniprogram_ignores_an_out_of_order_older_scope_response() -> None:
    script = r"""
const path = require("path");
const root = process.argv[1];
const apiPath = path.join(root, "miniprogram/services/api.js");
const pagePath = path.join(root, "miniprogram/pages/supervisor/dashboard.js");
const api = require(apiPath);
let pageDefinition = null;
global.Page = (definition) => { pageDefinition = definition; };
require(pagePath);

const pending = [];
api.operationsOverview = (scopeType, tenantId, propertyId) =>
  new Promise((resolve) => pending.push({ scopeType, tenantId, propertyId, resolve }));
const page = {
  ...pageDefinition,
  data: {
    ...pageDefinition.data,
    tenantId: "1",
    propertyId: "10",
    scopeOptions: [{ value: "PROPERTY", label: "当前小区" }],
    scopeIndex: 0,
  },
  setData(update) { this.data = { ...this.data, ...update }; },
};

const first = page.fetchOverview().catch(() => {});
page.data.propertyId = "20";
const second = page.fetchOverview().catch(() => {});
pending[1].resolve({
  scope_type: "PROPERTY", tenant_id: 1, property_id: 20,
  work_orders: { by_status: [], by_category: [], by_priority: [] },
  incidents: { by_status: [] }, sla_clocks: { by_state: [] },
});
Promise.resolve()
  .then(() => pending[0].resolve({
    scope_type: "PROPERTY", tenant_id: 1, property_id: 10,
    work_orders: { by_status: [], by_category: [], by_priority: [] },
    incidents: { by_status: [] }, sla_clocks: { by_state: [] },
  }))
  .then(() => Promise.all([first, second]))
  .then(() => {
    if (!page.data.overview || Number(page.data.overview.property_id) !== 20) {
      throw new Error("older response replaced the current scope");
    }
    if (page.data.loading !== false) {
      throw new Error("latest request did not own loading state");
    }
  });
"""
    completed = subprocess.run(
        ["node", "-e", script, str(REPO_ROOT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_miniprogram_hides_old_snapshot_before_scope_request_completes() -> None:
    script = r"""
const path = require("path");
const root = process.argv[1];
const api = require(path.join(root, "miniprogram/services/api.js"));
let pageDefinition = null;
global.Page = (definition) => { pageDefinition = definition; };
require(path.join(root, "miniprogram/pages/supervisor/dashboard.js"));
api.operationsOverview = () => new Promise(() => {});
const page = {
  ...pageDefinition,
  scopes: [],
  data: {
    ...pageDefinition.data,
    tenantId: "1",
    propertyId: "10",
    properties: [
      { id: 10, tenant_id: 1, name: "A" },
      { id: 20, tenant_id: 1, name: "B" },
    ],
    scopeOptions: [
      { value: "PROPERTY", label: "当前小区" },
      { value: "TENANT", label: "企业汇总" },
    ],
    scopeIndex: 0,
    overview: { scope_type: "PROPERTY", tenant_id: 1, property_id: 10 },
  },
  setData(update) { this.data = { ...this.data, ...update }; },
};
page.onPropertyChange({ detail: { value: "1" } });
if (page.data.overview !== null) process.exit(2);
const tenantOverview = { scope_type: "TENANT", tenant_id: 1, property_id: null };
page.data.scopeIndex = 1;
page.data.overview = tenantOverview;
page.onPropertyChange({ detail: { value: "0" } });
if (page.data.overview !== tenantOverview) process.exit(3);
page.data.scopeIndex = 0;
page.data.overview = { scope_type: "PROPERTY", tenant_id: 1, property_id: 10 };
page.onScopeChange({ detail: { value: "1" } });
if (page.data.overview !== null) process.exit(4);
"""
    completed = subprocess.run(
        ["node", "-e", script, str(REPO_ROOT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
