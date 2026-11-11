"""Evidence of gas smell cannot bypass the existing GAS/SAFETY manual gate."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.domain.ai import AiRisk
from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


@pytest.mark.parametrize('risk', ['none', 'electric', 'flooding', 'other', 'gas', 'safety'])
def test_observed_gas_smell_requires_existing_manual_risk_category(risk, monkeypatch):
    import app.service.analysis.validation as module
    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    text = '厨房现在有燃气气味。'
    raw = dict(category='other', observations=[dict(label='疑似燃气气味', subject='未确定', evidence_id='report:1')],
        severity='high', priority='p1', summary=text, risk=risk, required_role='STAFF',
        required_skill='maintenance', confidence=1.0)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider='qwen', ai_confidence_threshold=0.0)
    mapped, error = service._validate_payload(raw, request_id='synthetic-risk-consistency',
        evidence_catalog=build_evidence_catalog(report_text=text, asr_text=None, vision_text=None))
    if risk in {'gas', 'safety'}:
        assert error is None and mapped.risk == AiRisk(risk)
        logger.warning.assert_not_called()
    else:
        assert mapped is None and error == 'AI.INVALID_OUTPUT'
        logger.warning.assert_called_once_with('analysis_payload_rejected',
            request_id='synthetic-risk-consistency', error_code='AI.INVALID_OUTPUT',
            reason_code='risk_observation_conflict')
    assert raw['risk'] == risk  # Never silently rewrite original model claims.
