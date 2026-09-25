"""New category-derived execution contract, original evidence remains immutable."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError
from app.core.config import Settings
from app.domain.ai import (WorkOrderAnalysisOutput, map_analysis_output, DispatchCandidate,
    hard_filter_candidate, FilterReason, ordinary_maintenance, AiRisk)
from app.model.ai import WorkOrderAnalysis
from app.service.analysis import AnalysisService
from app.service.auto_dispatch import AutoDispatchService

def output(**kw):
    return WorkOrderAnalysisOutput(**(dict(category='cleaning',subcategory='纸盒清扫',severity='low',priority='p4',
        summary='楼道纸盒需要清扫；通行正常，无其他异常。',risk='none',required_role='STAFF',required_skill='maintenance',confidence=.95)|kw))
def candidate(duty,skill):
    return DispatchCandidate(employee_id=7,duty_codes=frozenset({duty}),skill_codes=frozenset({skill}),
        current_load=0,max_capacity=1,on_duty=True,employee_active=True,property_effective=True,has_staff_role=True)

@pytest.mark.parametrize('category,duty',[('cleaning','CLEANING'),('security','SECURITY')])
def test_known_generic_conflict_derives_only_existing_duty_and_preserves_raw(category,duty):
    raw=output(category=category); before=raw.model_dump();mapped=map_analysis_output(raw)
    assert raw.model_dump()==before and mapped.raw_required_skill=='maintenance'
    assert mapped.required_skill==mapped.required_duty==duty and mapped.skill_mapping_applied
    assert hard_filter_candidate(candidate(duty,duty),required_duty=mapped.required_duty,required_skill=mapped.required_skill) is None
    assert hard_filter_candidate(candidate('MAINTENANCE','MAINTENANCE'),required_duty=mapped.required_duty,required_skill=mapped.required_skill)==FilterReason.DUTY_MISMATCH
    other='SECURITY' if duty=='CLEANING' else 'CLEANING'
    assert hard_filter_candidate(candidate(other,other),required_duty=mapped.required_duty,required_skill=mapped.required_skill)==FilterReason.DUTY_MISMATCH
    assert not ordinary_maintenance(mapped.required_duty,mapped.required_skill)
    assert (mapped.summary,mapped.risk.value,mapped.severity.value,mapped.confidence)==(raw.summary,raw.risk,'LOW',raw.confidence)

def test_consistent_output_not_claimed_as_corrected():
    m=map_analysis_output(output(required_skill='cleaning'))
    assert not m.skill_mapping_applied and m.raw_required_skill=='cleaning'

@pytest.mark.parametrize('category',['cleaning','security','other'])
def test_special_restricted_skill_is_never_erased(category):
    m=map_analysis_output(output(category=category,required_skill='HAZMAT_LICENSE'))
    assert m.required_skill=='HAZMAT_LICENSE' and not m.skill_mapping_applied
    assert hard_filter_candidate(candidate(m.required_duty,m.required_duty),required_duty=m.required_duty,required_skill=m.required_skill)==FilterReason.SKILL_MISMATCH

@pytest.mark.parametrize('kw',[{'category':'other','required_skill':'other'}, {'confidence':.1}])
def test_insufficient_or_low_confidence_not_promoted(kw):
    assert map_analysis_output(output(**kw)).low_confidence

@pytest.mark.parametrize('risk',['gas','safety'])
def test_risk_guard_not_overridden_by_derived_skill(risk):
    m=map_analysis_output(output(risk=risk))
    assert m.risk==AiRisk(risk)
    assert AutoDispatchService(None,settings=Settings(_env_file=None,ai_auto_assign=True)).maybe_assign(work_order=object(),mapped=m,request_id='offline')=='HIGH_RISK'

def test_stored_original_roundtrip_uses_same_mapper_for_recommendation_and_redispatch():
    m=map_analysis_output(output());stored=WorkOrderAnalysis()
    AnalysisService._fill_analysis(stored,m)
    assert stored.required_skill=='maintenance'
    service=AnalysisService.__new__(AnalysisService);service._settings=Settings(_env_file=None)
    loaded=service._mapped_from_stored_analysis(stored)
    assert loaded.required_skill==m.required_skill and loaded.required_duty==m.required_duty
    assert loaded.raw_required_skill==m.raw_required_skill and loaded.dispatch_contract_version==m.dispatch_contract_version

def test_strict_other_contracts_still_rejected():
    with pytest.raises(ValidationError):output(extra=1)
    for kw in ({'risk':'invalid'},{'priority':'invalid'},{'required_role':'ADMIN'}):
        with pytest.raises(ValueError):map_analysis_output(output(**kw))

def test_audit_separates_original_derived_and_version():
    from app.service.audit import AuditService
    m=map_analysis_output(output());s=AnalysisService.__new__(AnalysisService)
    repo=MagicMock();s._audit=AuditService(MagicMock(),repository=repo)
    s._append_success_audit(SimpleNamespace(id=2,tenant_id=3,property_id=4),SimpleNamespace(id=5,status='COMPLETED'),'offline',m)
    saved=repo.append.call_args.args[0].details_json
    assert saved['raw_required_skill']=='maintenance' and saved['effective_required_skill']=='CLEANING'
    assert saved['skill_mapping_applied'] is True and saved['dispatch_contract_version']=='category-duty-skill-v1'

def test_low_confidence_keeps_raw_fields_but_has_no_effective_dispatch_fields():
    from app.service.audit import AuditService
    m=map_analysis_output(output(category='other',required_skill='maintenance',confidence=.3))
    s=AnalysisService.__new__(AnalysisService);repo=MagicMock();s._audit=AuditService(MagicMock(),repository=repo)
    s._append_success_audit(SimpleNamespace(id=2,tenant_id=3,property_id=4),SimpleNamespace(id=5,status='LOW_CONFIDENCE'),'offline',m)
    saved=repo.append.call_args.args[0].details_json
    assert saved['decision_code']=='CLARIFICATION_REQUIRED'
    assert saved['dispatch_fields_applied'] is False
    assert saved['raw_category']=='other' and saved['raw_required_skill']=='maintenance'
    assert saved['effective_category'] is None and saved['effective_required_skill'] is None

def test_low_confidence_never_selects_or_ranks_employee_recommendations():
    m=map_analysis_output(output(category='other',required_skill='maintenance',confidence=.3))
    s=AnalysisService.__new__(AnalysisService);s._ai=MagicMock();s._clock=MagicMock()
    s._work_orders=MagicMock();s._settings=Settings(_env_file=None)
    s._write_recommendations(SimpleNamespace(id=2),SimpleNamespace(id=5),m,None)
    s._ai.delete_recommendations.assert_called_once_with(5)
    s._ai.list_property_employees.assert_not_called()
    s._work_orders.employee_global_occupancy.assert_not_called()

def test_low_confidence_risk_is_preserved_while_dispatch_fields_are_ignored():
    m=map_analysis_output(output(category='other',required_skill='maintenance',confidence=.3,risk='gas'))
    assert m.low_confidence and m.risk==AiRisk.GAS and not m.dispatch_fields_applied
    service=AutoDispatchService(None,settings=Settings(_env_file=None,ai_auto_assign=True))
    assert service.maybe_assign(work_order=object(),mapped=m,request_id='offline')=='LOW_CONFIDENCE'

def test_unknown_category_is_invalid_even_when_confidence_is_low():
    raw = output(
        category='noise',
        required_skill='maintenance',
        confidence=.6,
        summary='夜里听到嗡嗡声，但来源未确定。',
    )
    with pytest.raises(ValueError, match="category is not a supported value"):
        map_analysis_output(raw)
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider='development')
    mapped, error = service._validate_payload(raw.model_dump())
    assert mapped is None and error == 'AI.INVALID_OUTPUT'
