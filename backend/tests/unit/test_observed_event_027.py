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

def test_r3fix_request03_current_observations_map_without_rewriting_raw_failure():
    import json
    from pathlib import Path
    text='插座正在连续冒火花，塑料外壳已经发黑，还有明显焦味；我没有触碰它，请尽快处理。'
    old=json.loads((Path(__file__).resolve().parents[3]/'reports/real-ai-integration-027/diagnostic/request-03/response.json').read_text(encoding='utf-8'))['provider_result'][0]
    with pytest.raises(ValueError,match='does not support'):
        bind(old,{'report':text})
    assert [x['label'] for x in old['observations']]==['火花烧损','表面剥落','疑似燃气气味']
    fixed=payload('插座正在连续冒火花',label='火花烧损',subject='插座')
    fixed['observations'] += [
        dict(label='烧焦变色',subject='未确定',source='report',quote='塑料外壳已经发黑'),
        dict(label='焦煳气味',subject='未确定',source='report',quote='还有明显焦味'),
    ]
    key,trace=bind(fixed,{'report':text})
    assert key.startswith('evt1:')
    assert [x['label'] for x in trace['observations']]==['火花烧损','烧焦变色','焦煳气味']
    assert trace['raw_model_output']==fixed

def test_r3fix1_real_answer_stays_raw_and_is_rejected_for_cross_clause_subject_inference():
    import json
    from pathlib import Path
    record=json.loads((Path(__file__).resolve().parents[3]/'reports/real-ai-integration-027/diagnostic-r3fix1/request-01/response.json').read_text(encoding='utf-8'))
    original=record['provider_result'][0]
    with pytest.raises(ValueError,match='Subject not supported'):
        bind(original,{'report':record['case']['report']})
    assert original['observations'][1]['subject']=='插座'
    assert original['observations'][1]['quote']=='塑料外壳已经发黑'

def test_unknown_subject_expresses_confirmed_risk_without_becoming_an_event_wildcard():
    black=payload('塑料外壳已经发黑',label='烧焦变色',subject='未确定')
    smell=payload('还有明显焦味',label='焦煳气味',subject='未确定')
    black['risk']='electric';black['severity']='high';black['priority']='p1'
    first_key,first_trace=bind(black,{'report':black['summary']})
    second_key,_=bind(smell,{'report':smell['summary']})
    assert first_key!=second_key
    assert first_trace['observations'][0]['subject']=='未确定'
    s=AnalysisService.__new__(AnalysisService);s._settings=Settings(_env_file=None,llm_provider='production')
    mapped,error=s._validate_payload(black,sources={'report':black['summary']})
    assert error is None and mapped.required_duty=='MAINTENANCE'
    assert mapped.risk.value=='electric'

def test_unknown_subject_evidence_text_prevents_cross_object_coalescing():
    a=payload('左侧外壳已经发黑',label='烧焦变色',subject='未确定')
    b=payload('右侧外壳已经发黑',label='烧焦变色',subject='未确定')
    assert bind(a,{'report':a['summary']})[0]!=bind(b,{'report':b['summary']})[0]

def test_explicit_multiple_subjects_bind_only_to_their_own_clauses():
    value=payload('水龙头持续滴水',label='滴渗水',subject='水龙头')
    value['observations']+=payload('插座正在冒火花',label='火花烧损',subject='插座')['observations']
    text='水龙头持续滴水，插座正在冒火花'
    assert bind(value,{'report':text})[0]
    value['observations'][0]['subject']='插座'
    with pytest.raises(ValueError,match='Subject not supported'):
        bind(value,{'report':text})

@pytest.mark.parametrize('text',['没有发现外壳发黑','上次外壳发黑','我怀疑外壳发黑'])
def test_unknown_subject_does_not_bypass_negation_history_or_uncertainty(text):
    with pytest.raises(ValueError):
        bind(payload('外壳发黑',label='烧焦变色',subject='未确定'),{'report':text})

@pytest.mark.parametrize('label,quote',[('表面剥落','塑料外壳已经发黑'),('疑似燃气气味','还有明显焦味')])
def test_r3fix_old_request03_nearest_wrong_labels_remain_rejected(label,quote):
    with pytest.raises(ValueError,match='does not support'):
        bind(payload(quote,label=label,subject='插座' if label=='表面剥落' else '未确定'),{'report':quote})

@pytest.mark.parametrize('label,text',[('烧焦变色','插座发黑'),('焦煳气味','插座有焦味')])
@pytest.mark.parametrize('prefix',['没有','上次','我怀疑'])
def test_r3fix_new_observations_do_not_accept_negated_historical_or_guessed_evidence(label,text,prefix):
    with pytest.raises(ValueError):
        bind(payload(text,label=label,subject='插座'),{'report':prefix+text})

def test_r3fix_burning_smell_and_gas_smell_are_distinct_observations():
    burning=payload('插座有烧焦味',label='焦煳气味',subject='插座')
    gas=payload('厨房有明显燃气气味',label='疑似燃气气味',subject='未确定')
    assert bind(burning,{'report':burning['summary']})[0]
    assert bind(gas,{'report':gas['summary']})[0]
    with pytest.raises(ValueError):bind(payload('插座有烧焦味',label='疑似燃气气味',subject='插座'),{'report':'插座有烧焦味'})

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
