"""Real evaluation regressions: synonyms and genuinely unspecified reports."""
from types import SimpleNamespace
import json
import pytest
from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService

def validate(text,*,label='现象待核实',subject='未确定',extra_report=None,asr=None,vision=None,**overrides):
    service=object.__new__(AnalysisService)
    service._settings=SimpleNamespace(llm_provider='qwen',ai_confidence_threshold=0.0)
    output=dict(category='other',observations=[dict(label=label,subject=subject,evidence_id='report:1')],
                severity='low',priority='p4',summary=text,risk='none',required_role='STAFF',required_skill='other',confidence=.1)
    output.update(overrides)
    catalog=build_evidence_catalog(report_text=text+('。'+extra_report if extra_report else ''),asr_text=asr,vision_text=vision)
    return service._validate_payload(output,evidence_catalog=catalog)

@pytest.mark.parametrize('text',['公共走廊有散落纸屑。','走廊有散落垃圾。','二楼走廊堆着纸盒，请清扫。'])
def test_corridor_is_existing_hallway_subject_synonym(text):
    mapped,error=validate(text,label='杂物清扫',subject='楼道地面',category='cleaning',required_skill='cleaning',confidence=.95)
    assert error is None and mapped.required_duty=='CLEANING' and not mapped.low_confidence

@pytest.mark.parametrize('text',['有问题，帮我处理。','出了点问题，麻烦看看。','有点不对劲，请帮忙看一下。'])
def test_unspecified_current_report_clarifies_even_at_zero_threshold(text):
    mapped,error=validate(text)
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.event_trace['effective_observations']==[]
    assert mapped.event_trace['informational_observations'][0]['subject']=='未确定'

@pytest.mark.parametrize('kwargs',[
    {'extra_report':'插座冒火花。'}, {'asr':'厨房有燃气气味。'},
    {'vision':'插座发黑。'}, {'extra_report':'水龙头滴水。'},
    {'category':'electrical','required_skill':'maintenance'},
    {'subject':'插座'}, {'risk':'safety'},
])
def test_generic_report_cannot_hide_specific_evidence_or_supply_dispatch_fields(kwargs):
    mapped,error=validate('有问题，帮我处理。',**kwargs)
    assert mapped is None and error=='AI.INVALID_OUTPUT'

@pytest.mark.parametrize('text',['走廊没有散落垃圾。','上次走廊有垃圾。','并非有问题。'])
def test_synonyms_do_not_relax_negation_or_history(text):
    if '走廊' in text: mapped,error=validate(text,label='杂物清扫',subject='楼道地面',category='cleaning',required_skill='cleaning')
    else:mapped,error=validate(text)
    assert mapped is None and error=='AI.INVALID_OUTPUT'

def test_current_asr_vague_report_keeps_asr_provenance_and_never_dispatches():
    mapped,error=validate('',asr='出了点问题，请帮忙。',summary='需要补充问题信息',
        observations=[dict(label='现象待核实',subject='未确定',evidence_id='asr:1')])
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    info=mapped.event_trace['informational_observations'][0]
    assert info['source']=='asr' and info['acceptance_reason']=='UNSPECIFIED_CURRENT_REPORT'

def test_unrelated_asr_cannot_legitimize_a_false_report_source_id():
    mapped,error=validate('',asr='有问题，请帮忙。',summary='信息不足')
    assert mapped is None and error=='AI.INVALID_OUTPUT'

@pytest.mark.parametrize('detail',['燃气泄漏','有人受伤','电梯困人','墙体倒塌','危险内容不在任何既有词典'])
def test_generic_path_rejects_any_extra_non_generic_details(detail):
    mapped,error=validate('有问题，'+detail+'。')
    assert mapped is None and error=='AI.INVALID_OUTPUT'


@pytest.mark.parametrize('text',['请根据图片确认，现在无法描述故障。','现在无法描述故障。'])
def test_unable_to_describe_with_blank_image_is_non_dispatching_clarification(text):
    vision=json.dumps([dict(image_index=1,description='图像为纯白背景，未见任何部件或故障迹象。',error_code=None)],ensure_ascii=False)
    mapped,error=validate(text,vision=vision,confidence=.99,
        summary='用户无法描述故障，图片没有可见故障，需要补充信息。')
    assert error is None
    assert mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.event_trace['effective_observations']==[]
    assert mapped.event_trace['informational_observations'][0]['source']=='report'


def test_inability_to_describe_does_not_support_a_concrete_fault():
    mapped,error=validate('现在无法描述故障。',label='滴渗水',subject='水龙头',
        category='water_supply',required_skill='maintenance',confidence=.99)
    assert mapped is None and error=='AI.INVALID_OUTPUT'


def test_inability_to_describe_does_not_borrow_asr_evidence():
    mapped,error=validate('请帮忙。',asr='现在无法描述故障。')
    assert mapped is None and error=='AI.INVALID_OUTPUT'


@pytest.mark.parametrize('text',[
    '三楼楼梯口有一堆塑料包装袋，需要清理；请今天18点以后联系，暂时不要安排上门。',
    '三楼楼梯口有一堆塑料包装袋，需要清理。',
])
def test_packaging_litter_is_cleaning_evidence(text):
    mapped,error=validate(text,label='杂物清扫',subject='楼道地面',
        category='cleaning',required_skill='cleaning',confidence=.95)
    assert error is None and mapped.required_duty=='CLEANING' and not mapped.low_confidence
    assert mapped.summary==text


@pytest.mark.parametrize('text',[
    '三楼楼梯口没有包装袋。', '昨天三楼楼梯口有包装袋。', '可能三楼楼梯口有包装袋。',
])
def test_packaging_litter_cannot_turn_negation_history_or_guess_into_fact(text):
    mapped,error=validate(text,label='杂物清扫',subject='楼道地面',
        category='cleaning',required_skill='cleaning',confidence=.95)
    assert mapped is None and error=='AI.INVALID_OUTPUT'
