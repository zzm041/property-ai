"""A local lighting operation can omit its already explicit lamp subject."""
from types import SimpleNamespace

import pytest

from app.domain.observed_event import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text, source="report", index=1):
    raw = dict(category="electrical", observations=[dict(label="照明不亮", subject="照明",
        evidence_id=f"{source}:{index}")], severity="medium", priority="p3", summary=text,
        risk="none", required_role="STAFF", required_skill="maintenance", confidence=.95)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=.7)
    return service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text if source == "report" else None,
        asr_text=text if source == "asr" else None,
        vision_text=text if source == "vision" else None))


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text", [
    "现在坏的是走廊顶灯，打开以后不亮。",
    "顶灯，打开以后不亮。",
    "客厅照明，开启后还是不亮。",
    "卧室台灯，开了之后仍然不亮。",
])
def test_local_lighting_operation_retains_explicit_subject(text, source):
    mapped, error = validate(text, source)
    assert error is None and mapped.dispatch_fields_applied
    assert mapped.event_trace["effective_observations"][0]["subject"] == "照明"


def test_historical_smoke_does_not_erase_current_lighting_failure():
    text = ("前天书房插座冒烟，维修人员处理后已停止，之后没有再冒烟。"
            "现在坏的是走廊顶灯，打开以后不亮；顶灯没有冒烟，也没闻到焦味，原因还没查。")
    mapped, error = validate(text, index=2)
    assert error is None and mapped.dispatch_fields_applied
    assert mapped.risk == "none"
    assert len(mapped.event_trace["effective_observations"]) == 1


@pytest.mark.parametrize("text", [
    "顶灯。打开以后不亮。",
    "灯和插座，打开以后不亮。",
    "台灯和顶灯，打开以后不亮。",
    "顶灯，屏幕打开以后不亮。",
    "顶灯，盒子打开以后不亮。",
    "昨天顶灯，打开以后不亮。",
    "听说顶灯，打开以后不亮。",
    "不是顶灯，打开以后不亮。",
    "没有说顶灯，打开以后不亮。",
    "如果顶灯，打开以后不亮，请报修。",
    "如果这样，顶灯，打开以后不亮，请报修。",
    "顶灯，打开以后可能不亮。",
    "顶灯，打开以后没有不亮。",
    "顶灯，打开以后不亮但现在已经正常亮起来。",
    "顶灯，打开以后不亮的问题已经修好。",
    "顶灯，开关旁边的东西，打开以后不亮。",
])
def test_omission_does_not_bypass_certainty_or_subject_boundaries(text):
    mapped, error = validate(text)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text", [
    "听说，顶灯，打开以后不亮。",
    "据说，走廊顶灯，打开以后不亮。",
    "昨天，顶灯，打开以后不亮。",
    "台灯还有顶灯，打开以后不亮。",
    "不要打开顶灯，打开以后不亮。",
    "不确定是顶灯，打开以后不亮。",
])
def test_topic_must_be_one_confirmed_current_lamp(text):
    mapped, error = validate(text)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text", [
    "如果方便请先留言，顶灯，打开以后不亮。",
    "如果有空请发消息，走廊顶灯，打开以后不亮。",
    "如果方便请安排维修，顶灯，打开以后不亮。",
    "顶灯，打开以后不亮，请安排维修。",
    "请检查顶灯，打开以后不亮。",
])
def test_complete_contact_or_repair_request_does_not_qualify_fault(text):
    mapped, error = validate(text)
    assert error is None and mapped.dispatch_fields_applied


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
def test_explicit_current_topic_does_not_inherit_previous_repaired_history(source):
    mapped, error = validate("昨天修好了水龙头，现在是走廊顶灯，打开以后不亮。", source)
    assert error is None and mapped.dispatch_fields_applied
