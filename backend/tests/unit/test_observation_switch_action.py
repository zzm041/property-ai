"""WO23 regression: an explicit lamp switch action is current evidence."""
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text):
    # Exact successful PHONE-V26-AI-03 output shape, without a model call.
    payload = dict(category='electrical', confidence=.95,
        observations=[dict(evidence_id='report:1', label='照明不亮', subject='照明')],
        priority='p3', required_role='STAFF', required_skill='maintenance', risk='none', severity='low',
        summary='本次：卧室顶灯打开开关后不亮，其他房间灯正常。否定/未知：无异味、冒烟或漏水。')
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider='production', ai_confidence_threshold=.7)
    return service._validate_payload(payload, evidence_catalog=build_evidence_catalog(
        report_text=text, asr_text=None, vision_text=None))


@pytest.mark.parametrize('text', [
    '卧室顶灯打开开关后不亮，其他房间的灯正常。没有异味、冒烟或漏水。',
    '卧室顶灯打开了开关之后还是不亮。',
    '客厅照明开启开关后不亮。',
])
def test_explicit_switch_action_does_not_erase_current_lamp_failure(text):
    mapped, error = validate(text)
    assert error is None and mapped.dispatch_fields_applied
    assert mapped.category.value == 'ELECTRICAL' and mapped.required_duty == 'MAINTENANCE'
    assert mapped.required_skill == 'MAINTENANCE' and mapped.risk == 'none'
    assert mapped.event_trace['effective_observations'][0]['subject'] == '照明'


@pytest.mark.parametrize('text', [
    '如果卧室顶灯打开开关后不亮，请联系物业。',
    '卧室顶灯打开开关后是否不亮？',
    '听说卧室顶灯打开开关后不亮。',
    '昨天卧室顶灯打开开关后不亮。',
    '不是卧室顶灯打开开关后不亮。',
    '卧室顶灯没有打开开关后不亮。',
    '卧室顶灯打开开关后不亮的问题已经修好。',
    '空调打开开关后不亮。',
    '卧室顶灯旁边的屏幕打开开关后不亮。',
])
def test_switch_word_does_not_bypass_certainty_or_subject_rules(text):
    mapped, error = validate(text)
    assert mapped is None and error == 'AI.INVALID_OUTPUT'
