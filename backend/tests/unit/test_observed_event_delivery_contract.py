"""Observed facts may drive dispatch; uncertainty and inferred causes may not."""
from types import SimpleNamespace

import pytest

from app.domain.observed_event import build_evidence_catalog
from app.service.analysis import AnalysisService


def check(text, label, subject="未确定", *, confidence=.95, threshold=0.0, **fields):
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=threshold)
    raw = dict(category="other", observations=[dict(label=label, subject=subject, evidence_id="report:1")],
               severity="low", priority="p3", summary=text, risk="none", required_role="STAFF",
               required_skill="maintenance", confidence=confidence)
    raw.update(fields)
    mapped, error = service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text, asr_text=None, vision_text=None))
    return mapped, error, raw


@pytest.mark.parametrize("threshold", [0.0, .7, 1.0])
def test_informational_only_cannot_dispatch_even_with_confidence_one(threshold):
    mapped, error, raw = check("设备是否有故障目前不确定。", "现象待核实", confidence=1, threshold=threshold)
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.confidence == 1 and mapped.event_trace["raw_model_output"] == raw
    assert mapped.event_trace["effective_observations"] == []


@pytest.mark.parametrize("text", [
    "家人说还在滴水，我刚看又是干的，是否漏水目前没确认。",
    "昨天还在滴，今天暂时没再看到水，能否确认已经修好？",
])
def test_unconfirmed_current_condition_is_clarification_not_provider_failure(text):
    mapped, error, raw = check(text, "现象待核实", confidence=.3, category="water_supply")
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.event_trace["raw_model_output"] == raw


def test_current_uncertainty_does_not_borrow_subject_from_historical_clause():
    text = "前天接头有滴水，今天没再看到，是否修好了还没确认。"
    mapped, error, raw = check(text, "现象待核实", "排水接头", confidence=.4, category="water_supply")
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    trace = mapped.event_trace
    assert trace["raw_model_output"] == raw
    assert trace["informational_observations"][0]["subject"] == "未确定"
    assert trace["adjusted_observations"][0]["raw_subject"] == "排水接头"


def test_stairwell_cleaning_is_a_supported_common_area_subject():
    mapped, error, _ = check("楼梯口散落几个纸杯需要清扫。", "杂物清扫", "楼道地面",
        category="cleaning", required_skill="cleaning")
    assert error is None and mapped.required_duty == "CLEANING" and mapped.dispatch_fields_applied


@pytest.mark.parametrize("text,label,subject", [
    ("卧室灯不亮。", "照明不亮", "照明"),
    ("走廊照明无法点亮。", "照明不亮", "照明"),
    ("开关旋钮拧起来特别费力。", "操作阻力", "未确定"),
    ("阀门卡涩，拧不动。", "操作阻力", "阀门管道"),
    ("公共走廊地面持续进水，水已扩散至门口。", "进水扩散", "楼道地面"),
    ("客厅地板持续进水。", "进水扩散", "楼道地面"),
    ("地上不断进水。", "进水扩散", "楼道地面"),
])
def test_current_observable_facts_have_distinct_labels(text, label, subject):
    mapped, error, _ = check(text, label, subject)
    assert error is None and not mapped.low_confidence and mapped.dispatch_fields_applied
    assert mapped.event_trace["effective_observations"][0]["label"] == label


@pytest.mark.parametrize("text,label,subject", [
    ("卧室灯不亮。", "不能供电", "照明"),
    ("旋钮拧起来费力。", "松动", "未确定"),
    ("水龙头供水正常。", "进水扩散", "水龙头"),
    ("地面没有进水。", "进水扩散", "楼道地面"),
    ("上次地面持续进水。", "进水扩散", "楼道地面"),
    ("可能灯不亮。", "照明不亮", "照明"),
    ("上次阀门卡涩。", "操作阻力", "阀门管道"),
    ("旋钮没有卡涩。", "操作阻力", "未确定"),
    ("地面正常，水箱进水。", "进水扩散", "楼道地面"),
    ("地面旁边的水箱正常进水。", "进水扩散", "楼道地面"),
    ("灯旁边的显示屏不亮。", "照明不亮", "照明"),
    ("昨天灯不亮，今天恢复了。", "照明不亮", "照明"),
    ("前天地面进水，今天地面是干的。", "进水扩散", "楼道地面"),
    ("昨天阀门卡涩，今天正常。", "操作阻力", "阀门管道"),
    ("灯不亮已经修好了。", "照明不亮", "照明"),
])
def test_new_vocabulary_never_proves_cause_or_cross_clause_or_history(text, label, subject):
    mapped, error, _ = check(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"
