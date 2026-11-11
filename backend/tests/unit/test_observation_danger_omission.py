"""Confirmed danger categories cannot disappear into ordinary clarification."""
from copy import deepcopy

import pytest

from app.domain.ai import (
    ObservationBindingError, ObservedAnalysisOutput, bind_observations, build_evidence_catalog,
)


def output(observations):
    return ObservedAnalysisOutput.model_validate(dict(
        category="other", observations=[dict(label=label, subject=subject, evidence_id=eid)
            for label, subject, eid in observations], severity="high", priority="p1",
        summary="合成测试，不补造原因", risk="safety", required_role="STAFF",
        required_skill="maintenance", confidence=.9))


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text", [
    "插座现在冒烟，但我没有看到明火。",
    "插座正在冒烟，不知道原因。",
    "厨房有燃气气味，但我没有找到来源。",
    "插座正在冒烟请勿靠近，不知道怎么处理。",
    "厨房有燃气气味请不要开灯，不知道来源。",
])
def test_clear_danger_cannot_be_only_informational(text, source):
    catalog = build_evidence_catalog(**{f"{s}_text": text if source == s else None
                                       for s in ("report", "asr", "vision")})
    raw = output([("现象待核实", "未确定", f"{source}:1")])
    original = deepcopy(raw.model_dump())
    with pytest.raises(ObservationBindingError) as caught:
        bind_observations(raw, catalog)
    assert caught.value.reason_code == "confirmed_danger_omitted"
    assert text not in str(caught.value)
    assert raw.model_dump() == original


@pytest.mark.parametrize("asr,vision", [("插座冒烟。", None), (None, "厨房有燃气气味。")])
def test_ordinary_fault_cannot_hide_another_sources_danger(asr, vision):
    catalog = build_evidence_catalog(report_text="柜门关不上。", asr_text=asr, vision_text=vision)
    with pytest.raises(ObservationBindingError) as caught:
        bind_observations(output([("闭合失效", "柜门", "report:1")]), catalog)
    assert caught.value.reason_code == "confirmed_danger_omitted"


@pytest.mark.parametrize("text", [
    "插座没有冒烟，不知道哪里有问题。",
    "听说插座冒烟，不知道现场情况。",
    "昨天插座冒烟，不知道现在怎么样。",
    "插座冒烟的问题已经修好，不知道是否还要检查。",
    "我怀疑，插座冒烟，不知道是不是这样。",
    "上个月插座有问题，插座冒烟，不知道现在情况。",
    "燃气没有问题，厨房垃圾有异味，不知道是谁留下的。",
    "厨房有异味，但不是燃气，不知道来源。",
    "没有燃气气味，不知道是什么问题。",
    "如果插座冒烟请离开，不知道现场情况。",
    "一旦闻到燃气气味请联系物业，不知道现在情况。",
    "这里严禁明火，不知道需要找谁咨询。",
    "请防止设备起火，不知道联系人是谁。",
    "插座有没有冒烟，不知道现场情况。",
    "燃气气味检测仪需要检查，不知道型号。",
    "安全演练模拟插座冒烟，不知道安排时间。",
    "明火作业需要审批，不知道审批联系人。",
    "插座冒烟的话请联系物业，不知道联系人。",
])
def test_negation_history_hearsay_and_cross_clause_gas_do_not_require_danger(text):
    catalog = build_evidence_catalog(report_text=text, asr_text=None, vision_text=None)
    _, trace = bind_observations(output([("现象待核实", "未确定", "report:1")]), catalog)
    assert not trace["effective_observations"]


def test_repeated_danger_sources_do_not_require_more_observation_slots():
    catalog = build_evidence_catalog(report_text="插座冒烟。" * 7,
                                    asr_text="插座冒烟。", vision_text="插座冒烟。")
    _, trace = bind_observations(output([("火花烧损", "插座", "report:1")]), catalog)
    assert len(trace["effective_observations"]) == 1


def test_two_distinct_danger_categories_cannot_be_reduced_to_one():
    catalog = build_evidence_catalog(report_text="插座冒烟。厨房有燃气气味。",
                                    asr_text=None, vision_text=None)
    with pytest.raises(ObservationBindingError) as caught:
        bind_observations(output([("火花烧损", "插座", "report:1")]), catalog)
    assert caught.value.reason_code == "confirmed_danger_omitted"
    _, trace = bind_observations(output([("火花烧损", "插座", "report:1"),
        ("疑似燃气气味", "未确定", "report:2")]), catalog)
    assert len(trace["effective_observations"]) == 2
