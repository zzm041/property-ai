"""Gas observation/risk conflicts stay in the manual path even with available staff."""
import pytest
from sqlalchemy import select

from app.model.ai import DispatchRecommendation
from app.model.work_order import WorkOrder, WorkOrderAssignment
from app.service.analysis import AnalysisService
from tests.integration.conftest import commit_open_transaction
from tests.integration.test_ai_runtime import _check_in, _create_work_order, _settings
from tests.integration.work_order_test_data import create_work_order_graph


@pytest.mark.parametrize('risk,expected_status', [('none', 'FAILED'), ('gas', 'COMPLETED')])
def test_gas_observation_never_uses_ordinary_automatic_assignment(identity_session, risk, expected_status):
    graph = create_work_order_graph(identity_session)
    _check_in(identity_session, graph.employees.staff_a_user_id,
              graph.employees.property_1_id, 'synthetic-gas-checkin')
    record = _create_work_order(identity_session, user_id=graph.employees.resident_id,
                               house_id=graph.house_1_id, text='厨房现在有燃气气味。')
    work_id = record.id
    commit_open_transaction(identity_session)

    class Provider:
        model_version = 'synthetic-gas-risk'

        def structured_completion(self, **kwargs):
            return dict(category='other', observations=[dict(label='疑似燃气气味',
                subject='未确定', evidence_id='report:1')], severity='high', priority='p1',
                summary='厨房现在有燃气气味。', risk=risk, required_role='STAFF',
                required_skill='maintenance', confidence=1.0)

    analysis = AnalysisService(identity_session, settings=_settings(ai_auto_assign=True,
        ai_confidence_threshold=0.0), llm=Provider()).analyze_work_order(
            work_order_id=work_id, request_id='synthetic-gas-risk')
    assert analysis.status == expected_status
    if risk == 'none':
        assert analysis.error_code == 'AI.INVALID_OUTPUT'
    commit_open_transaction(identity_session)
    identity_session.expire_all()
    saved = identity_session.get(WorkOrder, work_id)
    assert saved.current_assignment_id is None
    if risk == 'none':
        assert saved.status == 'PENDING_ANALYSIS'
        assert identity_session.scalars(select(DispatchRecommendation).where(
            DispatchRecommendation.work_order_id == work_id)).all() == []
    assert identity_session.scalars(select(WorkOrderAssignment).where(
        WorkOrderAssignment.work_order_id == work_id)).all() == []
