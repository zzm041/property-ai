"""Persisted evidence-only clarification must survive retries without inference."""
from sqlalchemy import select

from app.model.ai import DispatchRecommendation, Incident, WorkOrderAnalysis
from app.model.work_order import WorkOrder, WorkOrderAssignment
from app.service.analysis import AnalysisService
from tests.integration.test_ai_runtime import _create_work_order, _settings
from tests.integration.work_order_test_data import create_work_order_graph
from tests.integration.conftest import commit_open_transaction


def test_high_confidence_without_confirmed_observation_stays_unassigned_after_retry(identity_session):
    graph = create_work_order_graph(identity_session)
    record = _create_work_order(identity_session, user_id=graph.employees.resident_id,
        house_id=graph.house_1_id, text="设备是否有故障目前不确定。")
    work_order_id = record.id

    class EvidenceOnlyProvider:
        model_version = "synthetic-evidence-only"
        calls = 0

        def structured_completion(self, **kwargs):
            self.calls += 1
            return dict(category="other", confidence=1.0, required_role="STAFF",
                required_skill="maintenance", risk="none", severity="low", priority="p4",
                summary="设备故障尚待确认。", observations=[dict(label="现象待核实",
                subject="未确定", evidence_id="report:1")])

    provider = EvidenceOnlyProvider()
    service = AnalysisService(identity_session, settings=_settings(ai_auto_assign=True,
        ai_confidence_threshold=0.0), llm=provider)
    first = service.analyze_work_order(work_order_id=work_order_id, request_id="synthetic-clarify-first")
    first_id = first.id
    commit_open_transaction(identity_session)
    identity_session.expire_all()
    saved = identity_session.get(WorkOrderAnalysis, first_id)
    assert saved.status == "LOW_CONFIDENCE" and float(saved.confidence) == 1.0
    # PA-217: the persisted column holds the canonical uppercase code, not the
    # provider spelling. app.domain.ai.enums normalizes "maintenance" ->
    # "MAINTENANCE" (PA-202) and map_analysis_output stores that (rules.py:1348-1368);
    # unit tests already pin the uppercase contract
    # (test_ai_policy.py:41 required_skill == "ELECTRICAL"). The old assertion
    # compared the raw provider word, so it could only pass while normalization
    # was missing.
    assert saved.category == "OTHER" and saved.required_skill == "MAINTENANCE"
    commit_open_transaction(identity_session)
    second = service.analyze_work_order(work_order_id=work_order_id, request_id="synthetic-clarify-repeat")
    assert second.id == first_id and provider.calls == 1
    commit_open_transaction(identity_session)
    assert service.sweep_waiting_dispatch() == {"scanned": 0, "assigned": 0, "waiting": 0}
    state = identity_session.get(WorkOrder, work_order_id)
    assert state.status == "PENDING_ANALYSIS" and state.current_assignment_id is None
    assert identity_session.scalars(select(WorkOrderAssignment).where(WorkOrderAssignment.work_order_id == work_order_id)).all() == []
    assert identity_session.scalars(select(DispatchRecommendation).where(DispatchRecommendation.work_order_id == work_order_id)).all() == []
    assert identity_session.scalars(select(Incident)).all() == []
