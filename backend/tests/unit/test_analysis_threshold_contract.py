"""Confidence configuration must have the same meaning on both analysis paths."""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.provider.llm import SUCCESS_ANALYSIS
from app.service.analysis import AnalysisService


@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("threshold,confidence", [(0.0, 0.0), (0.0, 0.5), (0.2, 0.1), (0.2, 0.2), (0.7, 0.5), (0.7, 0.7), (1.0, 0.99), (1.0, 1.0)])
def test_valid_threshold_is_honored_on_new_and_stored_analysis(stored, threshold, confidence):
    service = object.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, app_env="test", llm_provider="development", ai_confidence_threshold=threshold)
    payload = dict(SUCCESS_ANALYSIS, category="water_supply", required_skill="plumbing", confidence=confidence)
    if stored:
        mapped = service._mapped_from_stored_analysis(SimpleNamespace(**payload))
    else:
        mapped, error = service._validate_payload(payload)
        assert error is None
    assert mapped is not None
    assert mapped.low_confidence is (confidence < threshold)


@pytest.mark.parametrize("stored", [False, True])
def test_zero_threshold_does_not_bypass_unknown_category_clarification(stored):
    service = object.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, app_env="test", llm_provider="development", ai_confidence_threshold=0.0)
    payload = dict(SUCCESS_ANALYSIS, category="other", required_skill="other", confidence=1.0)
    mapped = service._mapped_from_stored_analysis(SimpleNamespace(**payload)) if stored else service._validate_payload(payload)[0]
    assert mapped is not None and mapped.low_confidence
