"""PA-001: "无法断电" is a limitation on isolating power, not a 不能供电 fault.

The resident's report must keep its two real observations, the limitation must
stay visible as a non-trusted trace entry, and a limitation-only result still
fails closed.  Genuine negations keep their existing guard.
"""
from types import SimpleNamespace

import pytest

from app.domain.observed_event import ObservedAnalysisOutput, bind_observations, build_evidence_catalog
from app.service.analysis import AnalysisService

REPORT = '客厅插座冒烟并伴有焦糊味，无法断电。'

# Verbatim payload the local model returned for this report (call-003 of the
# PA-001 replay); it used to fail the whole analysis as prefixed_negative.
MODEL_OUTPUT = {
    'category': 'electrical',
    'confidence': .95,
    'observations': [
        dict(evidence_id='report:1', label='冒烟', subject='插座'),
        dict(evidence_id='report:1', label='焦煳气味', subject='未确定'),
        dict(evidence_id='report:1', label='不能供电', subject='插座'),
    ],
    'priority': 'p1',
    'required_role': 'STAFF',
    'required_skill': 'maintenance',
    'risk': 'electric',
    'severity': 'high',
    'summary': '本次：客厅插座冒烟并伴有焦糊味，无法断电。',
}


def catalog(report_text=REPORT):
    return build_evidence_catalog(report_text=report_text, asr_text=None, vision_text=None)


def raw(label, subject, *, category='electrical', risk='none'):
    return dict(category=category, observations=[dict(label=label, subject=subject, evidence_id='report:1')],
                severity='high', priority='p1', summary='synthetic', risk=risk, required_role='STAFF',
                required_skill='maintenance', confidence=.9)


def test_pa001_payload_completes_with_supported_observations():
    key, result = bind_observations(ObservedAnalysisOutput.model_validate(MODEL_OUTPUT), catalog())

    assert key.startswith('evt2:')
    assert [(row['label'], row['subject']) for row in result['effective_observations']] == [
        ('冒烟', '插座'), ('焦煳气味', '未确定')]
    assert len(result['ignored_observations']) == 1
    ignored = result['ignored_observations'][0]
    assert (ignored['label'], ignored['subject']) == ('不能供电', '插座')
    assert ignored['reason_code'] == 'action_limitation_reported'
    assert ignored['disposition'] == 'NOT_TRUSTED_ACTION_LIMITATION'
    assert ignored['ignored_reason'] == 'reported limitation on cutting power is not an outage observation'
    assert '不能供电' not in [row['label'] for row in result['effective_observations']]
    assert result['raw_model_output'] == MODEL_OUTPUT


def test_pa001_payload_passes_service_validation():
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider='qwen', ai_confidence_threshold=.7)

    mapped, error = service._validate_payload(dict(MODEL_OUTPUT), request_id='pa001-test',
                                              evidence_catalog=catalog())

    assert error is None
    assert mapped is not None
    assert mapped.category.value == 'ELECTRICAL' and mapped.risk == 'electric'
    assert [row['label'] for row in mapped.event_trace['effective_observations']] == ['冒烟', '焦煳气味']


@pytest.mark.parametrize('text,label,subject,reason', [
    ('客厅插座没有冒烟。', '冒烟', '插座', 'prefixed_negative'),
    ('水龙头没有滴水。', '滴渗水', '水龙头', 'prefixed_negative'),
    ('客厅插座无法断电。', '不能供电', '插座', 'action_limitation_reported'),
])
def test_guards_still_reject(text, label, subject, reason):
    with pytest.raises(ValueError) as caught:
        bind_observations(ObservedAnalysisOutput.model_validate(raw(label, subject)), catalog(text))
    assert getattr(caught.value, 'reason_code', None) == reason
    assert text not in str(caught.value)


def test_limitation_is_ignored_only_while_supported_observations_carry_the_result():
    data = raw('不能供电', '插座', risk='electric')
    data['observations'].append(dict(label='冒烟', subject='插座', evidence_id='report:1'))

    _, result = bind_observations(ObservedAnalysisOutput.model_validate(data), catalog())
    assert [row['label'] for row in result['effective_observations']] == ['冒烟']
    assert [row['reason_code'] for row in result['ignored_observations']] == ['action_limitation_reported']


def test_plain_outage_evidence_still_binds():
    _, result = bind_observations(
        ObservedAnalysisOutput.model_validate(raw('不能供电', '插座', risk='electric')),
        catalog('客厅插座断电了。'))
    assert [row['label'] for row in result['effective_observations']] == ['不能供电']
