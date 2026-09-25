from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from app.core.config import Settings
from app.domain.observed_event import ObservedAnalysisOutput,bind_observations
from app.domain.iam import OrganizationScope,ScopeType
from app.domain.audit import AuditAction,AuditResourceType
from app.service.audit import AuditService
from app.service.analysis import AnalysisService

def payload(quote='厨房水龙头关紧后还在滴水',label='滴渗水',subject='水龙头',source='report'):
    return dict(category='water_supply',observations=[dict(label=label,subject=subject,source=source,quote=quote)],
                severity='low',priority='p4',summary=quote,risk='none',required_role='STAFF',required_skill='maintenance',confidence=.9)

def bind(value,source):return bind_observations(ObservedAnalysisOutput.model_validate(value),source)

def test_current_quote_and_key_are_bound_without_changing_raw_answer():
    value=payload();key,trace=bind(value,{'report':value['summary']})
    assert key.startswith('evt1:') and len(key)<=64 and trace['raw_model_output']==value
    assert trace['observations'][0]['span']==[0,len(value['summary'])]

def test_same_observable_phenomenon_paraphrase_maps_same_but_other_subject_does_not():
    a=payload();b=payload('水龙头一直滴水');c=payload('洗手盆接头有渗水',subject='排水接头')
    keys=[bind(v,{'report':v['summary']})[0] for v in (a,b,c)]
    assert keys[0]==keys[1] and keys[0]!=keys[2]

@pytest.mark.parametrize('text,quote',[
    ('水龙头没有漏水','漏水'),('水龙头不是漏水','漏水'),
    ('上次水龙头滴水','水龙头滴水'),('我怀疑水龙头滴水','水龙头滴水'),
    ('邻居说水龙头滴水','水龙头滴水'),('一人说水龙头滴水','水龙头滴水'),
])
def test_matching_words_do_not_prove_negation_history_guess_or_hearsay(text,quote):
    with pytest.raises(ValueError):bind(payload(quote),{'report':text})

def test_history_and_other_source_cannot_supply_current_quote():
    with pytest.raises(ValueError):bind(payload(),{'report':'现象说不清','history':'厨房水龙头关紧后还在滴水'})
    with pytest.raises(ValueError):bind(payload(source='report'),{'report':'现象说不清','vision':'厨房水龙头关紧后还在滴水'})

def test_vision_is_marked_and_does_not_assert_user_confirmation():
    v=payload('天花板可见水渍',label='水渍',subject='天花墙面',source='vision')
    _,trace=bind(v,{'report':'不知道是否漏水','vision':v['summary']})
    assert trace['observations'][0]['source']=='vision'

def test_door_disturbance_has_no_intrusion_motive_label():
    v=payload('陌生人反复按门铃并拍门',label='敲门按铃',subject='房门')
    key,trace=bind(v,{'report':v['summary']+'，现在已经离开，未进入屋内'})
    assert key and trace['observations'][0]['label']=='敲门按铃'
    v['observations'][0]['label']='unauthorized_access_attempt'
    with pytest.raises(ValidationError):ObservedAnalysisOutput.model_validate(v)

def test_all_faults_contribute_to_key_and_order_is_irrelevant():
    a=payload();a['observations']+=payload('卧室插座不能供电',label='不能供电',subject='插座')['observations']
    text='厨房水龙头关紧后还在滴水，卧室插座不能供电'
    b={**a,'observations':list(reversed(a['observations']))}
    assert bind(a,{'report':text})[0]==bind(b,{'report':text})[0]
    assert bind(a,{'report':text})[0]!=bind(payload(),{'report':text})[0]

def test_no_water_negative_surface_form_is_a_positive_observation():
    v=payload('浴室水龙头一点水都不出',label='不出水')
    assert bind(v,{'report':v['summary']})[0]

@pytest.mark.parametrize('text',['插座火花没有出现','插座火花并没有出现','插座火花从未出现'])
def test_review_027_r3_r1_02_postposed_negation_cannot_share_positive_key(text):
    with pytest.raises(ValueError,match='Postposed'):
        bind(payload(text,label='火花烧损',subject='插座'),{'report':text})
    positive=payload('插座出现火花',label='火花烧损',subject='插座')
    assert bind(positive,{'report':positive['summary']})[0]

def test_real_provider_requires_evidence_and_existing_analysis_has_effective_key():
    s=AnalysisService.__new__(AnalysisService);s._settings=Settings(_env_file=None,llm_provider='production')
    v=payload();mapped,error=s._validate_payload(v,sources={'report':v['summary']})
    assert error is None and mapped.required_duty=='MAINTENANCE' and mapped.event_trace['raw_model_output']==v
    legacy={k:val for k,val in v.items() if k!='observations'};legacy['subcategory']='outlet_spark'
    assert s._validate_payload(legacy)[0] is None
    row=SimpleNamespace();s._fill_analysis(row,mapped)
    assert row.subcategory==mapped.subcategory
    # Retry reuses stored, already validated subtype, not reinterpreted history.
    assert s._mapped_from_stored_analysis(row).subcategory==mapped.subcategory

def test_scoped_audit_retains_raw_fields_and_effective_event_mapping():
    s=AnalysisService.__new__(AnalysisService);s._settings=Settings(_env_file=None,llm_provider='production');s._audit=Mock()
    v=payload();m,_=s._validate_payload(v,sources={'report':v['summary']})
    s._append_success_audit(SimpleNamespace(id=5,tenant_id=1,property_id=2),SimpleNamespace(id=6,status='COMPLETED'),'unit',m)
    details=s._audit.append.call_args.kwargs['details']
    assert details['event_evidence']['raw_model_output']==v
    AuditService._validate_details(action=AuditAction.WORK_ORDER_AI_ANALYSIS_COMPLETED,resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
        scope=OrganizationScope(ScopeType.PROPERTY,tenant_id=1,property_id=2),details=details)

def test_existing_unedited_real_failure_cannot_be_accepted_as_v10():
    import json
    from pathlib import Path
    record=json.loads((Path(__file__).resolve().parents[3]/'reports/auth-identity-ai-001/local-qwen/crossfield-diagnostic-01/request-02/response.json').read_text(encoding='utf-8'))
    original=record['provider_result'][0]
    assert original['subcategory']=='unauthorized_access_attempt'
    s=AnalysisService.__new__(AnalysisService);s._settings=Settings(_env_file=None,llm_provider='production')
    assert s._validate_payload(original,sources={'report':record['case']['report']})[0] is None
    # Proposed software evidence mapping, NOT a revised model answer or PASS.
    observation=payload('刚才有陌生人在楼道反复按门铃并拍门',label='敲门按铃',subject='房门')
    assert bind(observation,{'report':record['case']['report']})[1]['observations'][0]['label']=='敲门按铃'
