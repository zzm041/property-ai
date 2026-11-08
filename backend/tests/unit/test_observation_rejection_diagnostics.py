"""Rejection diagnostics describe guards, not a claim about semantic truth."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.domain.observed_event import ObservedAnalysisOutput, bind_observations, build_evidence_catalog
from app.service.analysis import AnalysisService


def raw(label='滴渗水', subject='水龙头', evidence_id='report:1'):
    return dict(category='water_supply', observations=[dict(label=label, subject=subject, evidence_id=evidence_id)],
                severity='low', priority='p3', summary='synthetic', risk='none', required_role='STAFF',
                required_skill='maintenance', confidence=.9)


@pytest.mark.parametrize('text,label,subject,evidence_id,reason', [
    ('水龙头滴水。', '滴渗水', '水龙头', 'report:9', 'unknown_evidence'),
    ('门合不上。', '操作阻力', '房门', 'report:1', 'label_not_matched'),
    ('墙面滴水。', '滴渗水', '水龙头', 'report:1', 'subject_conflict'),
    ('水龙头没有滴水。', '滴渗水', '水龙头', 'report:1', 'prefixed_negative'),
    ('水龙头滴水并没有发生。', '滴渗水', '水龙头', 'report:1', 'postposed_negative'),
    ('昨天水龙头滴水。', '滴渗水', '水龙头', 'report:1', 'historical'),
    ('听说水龙头滴水。', '滴渗水', '水龙头', 'report:1', 'uncertain'),
])
def test_binding_rejection_retains_machine_readable_reason(text, label, subject, evidence_id, reason):
    catalog = build_evidence_catalog(report_text=text, asr_text=None, vision_text=None)
    with pytest.raises(ValueError) as caught:
        bind_observations(ObservedAnalysisOutput.model_validate(raw(label, subject, evidence_id)), catalog)
    assert getattr(caught.value, 'reason_code', None) == reason
    assert text not in str(caught.value)


def test_invalid_catalog_has_separate_reason():
    with pytest.raises(ValueError) as caught:
        bind_observations(ObservedAnalysisOutput.model_validate(raw()),
                          {'report:1': {'source': 'vision', 'text': '水龙头滴水。'}})
    assert getattr(caught.value, 'reason_code', None) == 'invalid_catalog'


@pytest.mark.parametrize('invalid_schema', [False, True])
def test_service_logs_request_and_reason_without_raw_content(monkeypatch, invalid_schema):
    import app.service.analysis as module
    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider='qwen', ai_confidence_threshold=.7)
    data = raw('操作阻力', '房门')
    data['summary'] = 'sensitive-synthetic-content'
    if invalid_schema:
        data['confidence'] = 'sensitive-invalid-number'
    mapped, error = service._validate_payload(data, request_id='diagnostic-test',
        evidence_catalog=build_evidence_catalog(report_text='门合不上。', asr_text=None, vision_text=None))
    assert mapped is None and error == 'AI.INVALID_OUTPUT'
    logger.warning.assert_called_once_with('analysis_payload_rejected', request_id='diagnostic-test',
        error_code='AI.INVALID_OUTPUT', reason_code='schema_invalid' if invalid_schema else 'label_not_matched')
    assert 'sensitive' not in str(logger.mock_calls)
