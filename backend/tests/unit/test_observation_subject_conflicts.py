"""A valid observation must not launder another object's unsupported binding."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.observed_event import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text, observations, source, *, risk="none"):
    raw = dict(category="other", observations=[dict(label=label, subject=subject,
               evidence_id=f"{source}:1") for label, subject in observations],
               severity="low", priority="p3", summary=text, risk=risk,
               required_role="STAFF", required_skill="maintenance", confidence=1.0)
    original = deepcopy(raw)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    catalog = build_evidence_catalog(report_text=text if source == "report" else None,
        asr_text=text if source == "asr" else None, vision_text=text if source == "vision" else None)
    result = service._validate_payload(raw, evidence_catalog=catalog)
    assert raw == original
    return result


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("observations", [
    [("滴渗水", "水龙头"), ("松动", "水龙头")],
    [("松动", "水龙头"), ("滴渗水", "水龙头")],
])
def test_other_valid_fact_cannot_launder_competing_subject(source, observations):
    mapped, error = validate("水龙头滴水，扶手松动。", observations, source)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
def test_two_correctly_bound_objects_remain_usable(source):
    mapped, error = validate("水龙头滴水，扶手松动。",
        [("滴渗水", "水龙头"), ("松动", "扶手")], source)
    assert error is None and mapped.dispatch_fields_applied
    assert [(r["label"], r["subject"]) for r in mapped.event_trace["effective_observations"]] == [
        ("滴渗水", "水龙头"), ("松动", "扶手")]


def test_supported_match_is_selected_when_same_fault_has_two_subjects():
    mapped, error = validate("水龙头滴水，墙面滴水。",
        [("滴渗水", "水龙头"), ("滴渗水", "天花墙面")], "report")
    assert error is None
    assert {r["subject"] for r in mapped.event_trace["effective_observations"]} == {"水龙头", "天花墙面"}
    assert mapped.event_trace["adjusted_observations"] == []


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("location", ["楼道里", "楼道", "走廊", "楼梯口", "电梯厅", "通道"])
def test_location_word_is_not_a_competing_cause_of_smell(source, location):
    mapped, error = validate(f"插座冒烟，{location}有明显焦味。",
        [("火花烧损", "插座"), ("焦煳气味", "插座")], source, risk="electric")
    assert error is None
    assert [(r["label"], r["subject"]) for r in mapped.event_trace["effective_observations"]] == [
        ("火花烧损", "插座"), ("焦煳气味", "未确定")]


@pytest.mark.parametrize("text,label", [
    ("插座冒烟，空调有明显焦味。", "焦煳气味"),
    ("插座冒烟，墙面发黑。", "烧焦变色"),
    ("插座冒烟，地面持续进水。", "进水扩散"),
])
def test_explicit_other_equipment_or_surface_still_conflicts(text, label):
    mapped, error = validate(text, [("火花烧损", "插座"), (label, "插座")], "report", risk="electric")
    assert mapped is None and error == "AI.INVALID_OUTPUT"
