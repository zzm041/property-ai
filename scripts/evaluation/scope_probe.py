"""Synthetic, owned MySQL scope-review lifecycle; no resources on import."""
import json
from uuid import uuid4

from sqlalchemy import func, select

from app.domain.audit import AuditAction, AuditResourceType
from app.model.ai import AiCallLog, DispatchRecommendation, WorkOrderAnalysis
from app.model.employee import EmployeeDailyStatus
from app.model.identity import AuditLog
from app.model.work_order import WorkOrder, WorkOrderAssignment, WorkOrderContent, WorkOrderLog
from app.service.analysis import AnalysisService
from tests.integration.conftest import commit_open_transaction
from tests.integration.test_ai_runtime import _auth, _client, _token

INITIAL = "请帮朋友开通门禁。"
SCOPE_UPDATE = "请替我缴停车费。"
FAULT_UPDATE = "另外水龙头正在滴水，请先留言，收到回复后再电话联系，未经确认不要上门。"
PHASES = ("SQL_SCOPE_INITIAL", "SQL_SCOPE_UPDATE", "SQL_SCOPE_FAULT")


class ScopeStub:
    """Explicit offline provider, never an unlabelled model-quality result."""
    model_version = "scope-flow-synthetic-v22"

    def __init__(self):
        self.calls = 0

    def structured_completion(self, *, prompt, **kwargs):
        self.calls += 1
        evidence = json.loads(prompt.split("\nINPUT_DATA: ", 1)[1])["evidence_catalog"]
        repairs = [row for row in evidence if "水龙头正在滴水" in row["text"]]
        observations = [dict(label="范围外请求", subject="未确定", evidence_id=evidence[0]["id"])]
        if repairs:
            observations.append(dict(label="滴渗水", subject="水龙头", evidence_id=repairs[0]["id"]))
        return dict(category="water_supply" if repairs else "other", observations=observations,
            severity="low", priority="p4", risk="none", required_role="STAFF",
            required_skill="maintenance" if repairs else "other", confidence=1.0,
            summary=("请求开通门禁及代缴停车费；另有水龙头滴水。先留言，回复后电话联系，未经确认不上门。"
                     if repairs else "请求办理门禁或缴费事项，未执行，需物业核实办理渠道。"))


class ScopeProbe:
    def __init__(self, probe, *, provider):
        self.probe = probe
        self.provider = provider
        self.work_id = None
        self.analysis_ids = []
        self.finished_phases = []

    def _analyze(self, request_id):
        with self.probe.factory() as session:
            result = AnalysisService(session, settings=self.probe.settings, llm=self.provider).analyze_work_order(
                work_order_id=self.work_id, request_id=request_id)
            analysis_id = result.id if result is not None else None
            commit_open_transaction(session)
        return analysis_id

    def _read_sql(self):
        with self.probe.factory() as session:
            order = session.get(WorkOrder, self.work_id)
            content = session.scalar(select(WorkOrderContent).where(
                WorkOrderContent.work_order_id == self.work_id))
            analyses = session.scalars(select(WorkOrderAnalysis).where(
                WorkOrderAnalysis.work_order_id == self.work_id).order_by(WorkOrderAnalysis.id)).all()
            latest = analyses[-1] if analyses else None
            audits = session.scalars(select(AuditLog).where(
                AuditLog.resource_type == AuditResourceType.WORK_ORDER_ANALYSIS.value,
                AuditLog.action == AuditAction.WORK_ORDER_AI_ANALYSIS_COMPLETED.value,
                AuditLog.resource_id == latest.id)).all() if latest else []
            assignments = session.scalars(select(WorkOrderAssignment).where(
                WorkOrderAssignment.work_order_id == self.work_id)).all()
            return dict(work_id=order.id, work_status=order.status, work_version=order.version,
                assignment_id=order.current_assignment_id,
                assignments=[dict(id=row.id, employee_id=row.employee_id) for row in assignments],
                text=content.text, extra_notes=content.extra_notes,
                analyses=[dict(id=row.id, version=row.analysis_version, status=row.status,
                    error_code=row.error_code, summary=row.summary, confidence=float(row.confidence)
                    if row.confidence is not None else None) for row in analyses],
                recommendations=session.scalar(select(func.count()).select_from(DispatchRecommendation)
                    .where(DispatchRecommendation.analysis_id == latest.id)) if latest else 0,
                llm_log_count=session.scalar(select(func.count()).select_from(AiCallLog).where(
                    AiCallLog.work_order_id == self.work_id, AiCallLog.provider_type == "LLM")),
                staff_load=session.scalar(select(EmployeeDailyStatus.current_load).where(
                    EmployeeDailyStatus.employee_id == self.probe.graph.employees.employee_a_id)),
                audit_decisions=[row.details_json.get("decision_code") for row in audits],
                append_reasons=[row.payload_json.get("reason_code") for row in session.scalars(
                    select(WorkOrderLog).where(WorkOrderLog.work_order_id == self.work_id,
                                               WorkOrderLog.event_code == "APPEND")).all()])

    def _read_staff_state(self):
        with self.probe.factory() as session:
            rows = session.scalars(select(EmployeeDailyStatus).where(
                EmployeeDailyStatus.employee_id == self.probe.graph.employees.employee_a_id,
                EmployeeDailyStatus.property_id == self.probe.graph.employees.property_1_id)).all()
            return [dict(employee_id=row.employee_id, property_id=row.property_id,
                business_date=row.business_date.isoformat(), on_duty=row.on_duty,
                current_load=row.current_load) for row in rows]

    def phase(self, phase):
        index = PHASES.index(phase)
        if self.finished_phases != list(PHASES[:index]):
            raise ValueError("SCOPE_PHASE_PREREQUISITE_NOT_PASSED")
        staff_before = self._read_staff_state()
        if (len(staff_before) != 1 or staff_before[0]["on_duty"] != "ON_DUTY"
                or staff_before[0]["current_load"] != 0):
            return dict(id=phase, status="NOT_EXECUTED_PRECONDITION_FAILED", staff_before=staff_before,
                checks={"unique_on_duty_empty_staff": False}, provider_attempts=0,
                kind="scope_sql_precondition", limitation="No API write or model attempt; fixture state is unsuitable")
        checks = {}
        before = None
        after_append = None
        with _client(self.probe.settings) as client:
            resident = _token(client, self.probe.graph.employees.resident_id)
            supervisor = _token(client, self.probe.graph.employees.supervisor_p1_id)
            foreign = _token(client, self.probe.graph.resident_b_id)
            if index == 0:
                response = client.post("/api/v1/work-orders", json=dict(
                    house_id=self.probe.graph.house_1_id, contact_phone="13800000000", text=INITIAL),
                    headers={**_auth(resident, phase + "-create"), "Idempotency-Key": uuid4().hex})
                assert response.status_code in {200, 201}, "SCOPE_CREATE_FAILED"
                self.work_id = response.json()["data"]["id"]
                self.probe.work_ids.append(self.work_id)
                self.probe.case_ids[phase] = self.work_id
            else:
                before = self._read_sql()
                update = SCOPE_UPDATE if index == 1 else FAULT_UPDATE
                denied = client.post(f"/api/v1/work-orders/{self.work_id}/append", json=dict(
                    text="forbidden synthetic change", version=before["work_version"]),
                    headers=_auth(foreign, phase + "-append-denied"))
                checks["foreign_append_denied"] = denied.status_code == 403
                checks["denied_append_no_mutation"] = self._read_sql() == before
                conflict = client.post(f"/api/v1/work-orders/{self.work_id}/append", json=dict(
                    text=update, version=before["work_version"] + 1),
                    headers=_auth(resident, phase + "-append-conflict"))
                checks["conflicting_version_denied"] = conflict.status_code == 409
                checks["conflicting_append_no_mutation"] = self._read_sql() == before
                appended = client.post(f"/api/v1/work-orders/{self.work_id}/append", json=dict(
                    text=update, version=before["work_version"]), headers=_auth(resident, phase + "-append"))
                assert appended.status_code == 200, "SCOPE_APPEND_FAILED"
                after_append = self._read_sql()
                pending_detail = client.get(f"/api/v1/work-orders/{self.work_id}",
                                             headers=_auth(resident, phase + "-pending-read"))
                checks.update(old_analysis_stale=after_append["analyses"][-1]["status"] == "STALE",
                    append_version_incremented=after_append["work_version"] == before["work_version"] + 1,
                    old_scope_message_cleared=pending_detail.status_code == 200 and not
                        pending_detail.json()["data"]["display"].get("analysis_message"),
                    append_original_retained=after_append["text"] == INITIAL and update in after_append["extra_notes"],
                    accumulated_updates_exact=after_append["extra_notes"] == (
                        SCOPE_UPDATE if index == 1 else SCOPE_UPDATE + "\n" + FAULT_UPDATE),
                    invalidation_logged=after_append["append_reasons"][-1] == "SCOPE_REVIEW_INVALIDATED_BY_APPEND")
            analysis_id = self._analyze(phase + "-analyze")
            assert analysis_id is not None, "NEW_INPUT_ANALYSIS_MISSING"
            self.analysis_ids.append(analysis_id)
            saved = self._read_sql()
            if saved["analyses"][-1]["status"] == "FAILED":
                return dict(id=phase, status="FAIL", checks={"analysis_not_failed": False}, sql=saved,
                    staff_before=staff_before,
                    kind="real_model_sql_api" if self.probe.live else "synthetic_model_sql_api",
                    limitation="Failed analysis retained; no retry or dependent phase is attempted")
            repeated_id = self._analyze(phase + "-same-input")
            repeated = self._read_sql()
            checks.update(same_input_reused_or_assigned_noop=(repeated_id is None if saved["work_status"] == "ASSIGNED"
                else repeated_id == analysis_id),
                same_input_sql_unchanged=repeated == saved,
                same_input_no_provider_call=repeated["llm_log_count"] == saved["llm_log_count"],
                one_call_per_new_input=saved["llm_log_count"] == index + 1,
                same_input_no_additional_assignment=repeated["assignments"] == saved["assignments"],
                latest_analysis_version=saved["analyses"][-1]["version"] == index + 1,
                new_analysis_id=len(set(self.analysis_ids)) == len(self.analysis_ids),
                original_text_retained=saved["text"] == INITIAL)
            detail = client.get(f"/api/v1/work-orders/{self.work_id}", headers=_auth(resident, phase + "-read"))
            foreign_detail = client.get(f"/api/v1/work-orders/{self.work_id}", headers=_auth(foreign, phase + "-foreign"))
            protected = client.get(f"/api/v1/supervisor/work-orders/{self.work_id}/analysis",
                                   headers=_auth(resident, phase + "-analysis-denied"))
            diagnosis = client.get(f"/api/v1/supervisor/work-orders/{self.work_id}/diagnosis", params=dict(
                property_id=self.probe.graph.employees.property_1_id, version=saved["work_version"]),
                headers=_auth(supervisor, phase + "-diagnosis"))
            display = detail.json().get("data", {}).get("display", {})
            facts = diagnosis.json().get("data", {}).get("facts", [])
            checks.update(authenticated_detail=detail.status_code == 200,
                api_original_matches_sql=detail.json().get("data", {}).get("text") == saved["text"],
                api_updates_match_sql=detail.json().get("data", {}).get("extra_notes") == saved["extra_notes"],
                foreign_detail_denied=foreign_detail.status_code == 403,
                resident_analysis_denied=protected.status_code == 403,
                authorized_diagnosis=diagnosis.status_code == 200)
            if index < 2:
                checks.update(scope_reason_persisted=saved["analyses"][-1]["status"] == "LOW_CONFIDENCE" and
                    saved["analyses"][-1]["error_code"] == "AI.OUT_OF_SCOPE",
                    pending_no_assignment=saved["work_status"] == "PENDING_ANALYSIS" and not saved["assignments"],
                    no_recommendations=saved["recommendations"] == 0,
                    free_staff_not_used=saved["staff_load"] == 0,
                    manual_message="联系物业确认处理渠道" in (display.get("analysis_message") or ""),
                    scope_diagnosis=any(f["code"] == "OUT_OF_SCOPE_REVIEW_REQUIRED" for f in facts),
                    scope_audit="OUT_OF_SCOPE_REVIEW_REQUIRED" in saved["audit_decisions"])
            else:
                checks.update(repair_completed=saved["analyses"][-1]["status"] == "COMPLETED" and
                    saved["analyses"][-1]["error_code"] is None,
                    repair_automatically_assigned=saved["work_status"] == "ASSIGNED" and len(saved["assignments"]) == 1,
                    qualified_employee_assigned=bool(saved["assignments"]) and
                        saved["assignments"][0]["employee_id"] == self.probe.graph.employees.employee_a_id,
                    staff_load_incremented=saved["staff_load"] == 1,
                    old_message_gone=not display.get("analysis_message"),
                    old_scope_fact_gone=not any(f["code"] == "OUT_OF_SCOPE_REVIEW_REQUIRED" for f in facts),
                    normal_audit="DISPATCH_FIELDS_ACCEPTED" in saved["audit_decisions"],
                    historical_reasons_retained=all(a["status"] == "STALE" and a["error_code"] == "AI.OUT_OF_SCOPE"
                                                    for a in saved["analyses"][:-1]))
            status = "PASS" if all(checks.values()) else "FAIL"
            if status == "PASS":
                self.finished_phases.append(phase)
            return dict(id=phase, status=status, checks=checks, before=before, after_append=after_append,
                staff_before=staff_before,
                sql=saved, detail_display=display, diagnosis_facts=facts,
                kind="real_model_sql_api" if self.probe.live else "synthetic_model_sql_api",
                limitation="Owned disposable MySQL and authenticated ASGI; no live-network, Worker or device acceptance")
