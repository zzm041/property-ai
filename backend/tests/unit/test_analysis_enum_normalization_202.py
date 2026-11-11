"""PA-202: model-spelled enums fold deterministically, unknown ones stay rejected.

The 4B model answers ``PLUMBING``/``Water-Supply``/``HIGH``/``p0``/``plumbing``
where the contract spells ``water_supply``/``high``/``p1``/``maintenance``.
Before this table existed those payloads died in pydantic with the whole
analysis attached, so a usable report produced ``AI.INVALID_OUTPUT``.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.domain.ai import build_evidence_catalog
from app.domain.ai.enums import (
    EnumNormalization,
    EnumNormalizationError,
    enum_key,
    normalize_analysis_payload,
    normalize_enum,
)
from app.service.analysis import AnalysisService


def payload(**overrides):
    base = dict(
        category='water_supply',
        observations=[dict(label='滴渗水', subject='水龙头', evidence_id='report:1')],
        severity='low',
        priority='p3',
        summary='synthetic',
        risk='none',
        required_role='STAFF',
        required_skill='maintenance',
        confidence=.9,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize('raw, expected', [
    # category: upper, separator spelling, trade alias, sibling values
    ('PLUMBING', 'water_supply'),
    ('Water-Supply', 'water_supply'),
    ('plumbing', 'water_supply'),
    ('HVAC', 'hvac'),
    ('Air Conditioning', 'hvac'),
    ('ELECTRICAL', 'electrical'),
    ('Cleaning', 'cleaning'),
    ('SECURITY', 'security'),
    ('misc', 'other'),
])
def test_category_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('category', raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('HIGH', 'high'),
    ('Medium', 'medium'),
    ('minor', 'low'),
    ('SEVERE', 'high'),
])
def test_severity_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('severity', raw) == expected


@pytest.mark.parametrize('raw, expected', [
    # p0 is the same top grade spelled from zero; p1..p4 keep shipped meanings.
    ('P0', 'p1'),
    ('p0', 'p1'),
    ('URGENT', 'p1'),
    ('P2', 'p2'),
    ('Priority 3', 'p3'),
    ('lowest', 'p4'),
])
def test_priority_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('priority', raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('Flood', 'flooding'),
    ('GAS', 'gas'),
    ('electric_hazard', 'electric'),
    ('personal safety', 'safety'),
])
def test_risk_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('risk', raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('staff', 'STAFF'),
    ('Technician', 'STAFF'),
])
def test_role_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('required_role', raw) == expected


@pytest.mark.parametrize('raw, expected', [
    ('MAINTENANCE', 'maintenance'),
    ('Cleaning', 'cleaning'),
    ('SECURITY', 'security'),
])
def test_skill_spellings_fold_to_the_contract_value(raw, expected):
    assert normalize_enum('required_skill', raw) == expected


@pytest.mark.parametrize('label', ['Electrician', 'plumbing', 'HVAC technician', 'Guard', 'unknown', 'carpentry'])
def test_unknown_skill_labels_pass_through_untouched(label):
    """PA-167 keeps the raw label so dispatch keeps comparing against the trade.

    Folding "electrician" to "maintenance" would erase the restriction and let
    any maintenance group take an electrical job, so a trade label on the strict
    path stays rejected rather than silently downgraded.
    """
    assert normalize_enum('required_skill', label) is None
    # ... and the payload keeps the label so PA-167 can keep the restriction
    normalized, applied = normalize_analysis_payload(payload(required_skill=label))
    assert normalized['required_skill'] == label
    assert applied == ()


@pytest.mark.parametrize('field, raw', [
    ('category', 'kitchen_sink'),
    ('severity', 'catastrophic'),
    ('priority', 'p9'),
    ('risk', 'explosive'),
    ('required_role', 'MANAGER'),
])
def test_unknown_spellings_are_rejected_not_guessed(field, raw):
    # probing the tables reports "unmapped" without inventing a value ...
    assert normalize_enum(field, raw) is None
    # ... and a payload carrying one is refused, with the field named
    with pytest.raises(EnumNormalizationError) as caught:
        normalize_analysis_payload(payload(**{field: raw}))
    assert caught.value.field == field
    assert caught.value.raw == raw
    assert raw in str(caught.value)


def test_key_folding_is_separator_and_case_insensitive():
    assert enum_key('  Water-Supply  ') == 'water_supply'
    assert enum_key('AIR  CONDITIONING') == 'air_conditioning'
    assert enum_key('water/supply') == 'water_supply'
    assert enum_key('p 3') == 'p_3'


def test_canonical_payload_is_returned_untouched():
    data = payload()
    normalized, applied = normalize_analysis_payload(data)
    assert normalized == data
    assert applied == ()


def test_mixed_payload_folds_every_covered_field():
    normalized, applied = normalize_analysis_payload(
        payload(
            category='PLUMBING',
            severity='HIGH',
            priority='P2',
            risk='Flood',
            required_role='staff',
            required_skill='MAINTENANCE',
            summary='keep me',
        )
    )
    assert normalized['category'] == 'water_supply'
    assert normalized['severity'] == 'high'
    assert normalized['priority'] == 'p2'
    assert normalized['risk'] == 'flooding'
    assert normalized['required_role'] == 'STAFF'
    assert normalized['required_skill'] == 'maintenance'
    assert normalized['summary'] == 'keep me'
    assert normalized['observations'] == payload()['observations']
    assert [(item.field, item.raw, item.normalized) for item in applied] == [
        ('category', 'PLUMBING', 'water_supply'),
        ('severity', 'HIGH', 'high'),
        ('priority', 'P2', 'p2'),
        ('risk', 'Flood', 'flooding'),
        ('required_role', 'staff', 'STAFF'),
        ('required_skill', 'MAINTENANCE', 'maintenance'),
    ]
    assert all(isinstance(item, EnumNormalization) for item in applied)


def test_unmapped_payload_aborts_before_any_field_is_rewritten():
    data = payload(category='PLUMBING', severity='catastrophic')
    with pytest.raises(EnumNormalizationError) as caught:
        normalize_analysis_payload(data)
    # the caller keeps its own mapping: folding is all-or-nothing per payload
    assert caught.value.field == 'severity'
    assert data['category'] == 'PLUMBING'


def test_missing_and_non_string_fields_are_left_to_the_contract_model():
    normalized, applied = normalize_analysis_payload({'category': None, 'priority': '', 'severity': 3})
    assert normalized == {'category': None, 'priority': '', 'severity': 3}
    assert applied == ()


def _service():
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider='qwen', ai_confidence_threshold=.7)
    return service


def test_service_accepts_model_spelled_enum_and_maps_it(monkeypatch):
    import app.service.analysis.validation as module

    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    catalog = build_evidence_catalog(report_text='厨房水龙头滴水。', asr_text=None, vision_text=None)
    mapped, error = _service()._validate_payload(
        payload(category='PLUMBING', priority='P2', severity='HIGH', risk='Flood'),
        request_id='enum-normalized',
        evidence_catalog=catalog,
    )
    assert error is None and mapped is not None
    assert mapped.category.value == 'PLUMBING'
    assert mapped.priority.value == 'HIGH'
    assert mapped.severity.value == 'HIGH'
    assert mapped.risk.value == 'flooding'
    logger.info.assert_called_once()
    event, = logger.info.call_args.args
    assert event == 'analysis_enum_normalized'
    assert {item['field'] for item in logger.info.call_args.kwargs['applied']} == {
        'category', 'severity', 'priority', 'risk',
    }


def test_service_rejects_unmapped_enum_with_its_own_reason_code(monkeypatch):
    import app.service.analysis.validation as module

    logger = Mock()
    monkeypatch.setattr(module, 'logger', logger)
    catalog = build_evidence_catalog(report_text='厨房水龙头滴水。', asr_text=None, vision_text=None)
    mapped, error = _service()._validate_payload(
        payload(category='kitchen_sink', summary='sensitive-synthetic-content'),
        request_id='enum-unmapped',
        evidence_catalog=catalog,
    )
    assert mapped is None and error == 'AI.INVALID_OUTPUT'
    logger.warning.assert_called_once_with(
        'analysis_payload_rejected',
        request_id='enum-unmapped',
        error_code='AI.INVALID_OUTPUT',
        reason_code='enum_not_mapped',
    )
    assert 'sensitive' not in str(logger.mock_calls)
