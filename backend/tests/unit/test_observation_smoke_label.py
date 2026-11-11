"""Smoke has an explicit label while preserving legacy danger identity."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.domain.ai import (
    ObservationBindingError, ObservedAnalysisOutput, bind_observations,
    build_evidence_catalog, work_order_analysis_response_format,
)
from app.provider.llm import QwenLLMProvider
from app.service.analysis import AnalysisService


def payload(*rows, risk="electric", category="electrical"):
    return dict(category=category, observations=[dict(label=label, subject=subject, evidence_id=eid)
        for label, subject, eid in (rows or (("冒烟", "插座", "report:1"),))],
        severity="high", priority="p1", summary="合成冒烟报修，原因未确定",
        risk=risk, required_role="STAFF", required_skill="maintenance", confidence=.9)


def catalog(text, source="report"):
    return build_evidence_catalog(**{f"{s}_text": text if s == source else None
                                    for s in ("report", "asr", "vision")})


def bind(raw, evidence):
    original = deepcopy(raw)
    result = bind_observations(ObservedAnalysisOutput.model_validate(raw), evidence)
    assert raw == original
    return result


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("device", ["插座", "插排", "排插"])
def test_explicit_smoke_keeps_raw_label_and_current_evidence(source, device):
    text = f"{device}现在冒烟，但我没有看到明火。"
    raw = payload(("冒烟", "插座", f"{source}:1"))
    _, trace = bind(raw, catalog(text, source))
    assert trace["effective_observations"][0]["label"] == "冒烟"
    assert trace["effective_observations"][0]["quote"] == text
    assert trace["raw_model_output"] == raw


@pytest.mark.parametrize("text", [
    "插座没有冒烟。", "听说插座冒烟。", "昨天插座冒烟。",
    "插座冒烟的问题已经修好。", "如果插座冒烟请离开。",
    "安全演练模拟插座冒烟。", "请防止插座冒烟。", "插座冒烟的话请联系物业。",
    "插座有没有冒烟？", "插座外壳烫手。", "插座有焦味。", "插座旁有水蒸气。",
])
def test_noncurrent_or_different_phenomena_cannot_be_smoke(text):
    with pytest.raises(ObservationBindingError):
        bind(payload(), catalog(text))


def test_warning_after_observed_smoke_does_not_negate_it():
    _, trace = bind(payload(), catalog("插座正在冒烟请勿靠近，不知道怎么处理。"))
    assert len(trace["effective_observations"]) == 1


@pytest.mark.parametrize("label", ["冒烟", "火花烧损"])
@pytest.mark.parametrize("text", [
    "插座已经停止冒烟。", "插座现在不再冒烟。", "插座停止了冒烟。",
    "不是厨房插排正在冒烟，是热水蒸气。", "并非外壳正在冒烟。",
    "假设出现以下情况，插座正在冒烟，人已离开，该怎么办？",
    "如果发生故障，插座冒烟的话该怎么办？",
])
def test_ended_negated_and_adjacent_hypothetical_smoke_is_not_current(text, label):
    with pytest.raises(ObservationBindingError):
        bind(payload((label, "插座", "report:1")), catalog(text))


@pytest.mark.parametrize("text", ["插座已经停止冒烟。", "插座现在不再冒烟。",
    "不是厨房插排正在冒烟，是热水蒸气。", "假设发生故障，插座正在冒烟。"])
def test_noncurrent_smoke_does_not_force_a_danger_label_on_another_fault(text):
    evidence = catalog(text + "柜门合不上。")
    _, trace = bind(payload(("闭合失效", "柜门", "report:2")), evidence)
    assert trace["effective_observations"][0]["label"] == "闭合失效"


def test_legacy_fire_can_still_bind_current_sparks_after_ended_smoke():
    evidence = catalog("插座已经停止冒烟，但插座现在有火花。")
    _, trace = bind(payload(("火花烧损", "插座", "report:1")), evidence)
    assert trace["effective_observations"][0]["label"] == "火花烧损"


@pytest.mark.parametrize("text", [
    "插座现在冒烟，请勿靠近。", "如果方便请维修，插座正在冒烟。",
    "不知道原因，插座现在冒烟。", "插座现在冒烟，请停止使用。",
])
def test_requests_and_postposed_safety_actions_keep_current_smoke(text):
    assert bind(payload(), catalog(text))[1]["effective_observations"]


@pytest.mark.parametrize("risk", ["none", "other", "flooding", "electric", "gas", "safety"])
def test_service_preserves_existing_risk_gate(risk):
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    result, error = service._validate_payload(payload(risk=risk), evidence_catalog=catalog("插座冒烟。"))
    if risk in {"electric", "gas", "safety"}:
        assert error is None and result.risk.value == risk
    else:
        assert result is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("subject,text", [("插座", "插座冒烟。"), ("未确定", "外壳冒烟。")])
def test_old_new_and_duplicate_labels_have_same_event_identity(subject, text):
    evidence = catalog(text)
    old, _ = bind(payload(("火花烧损", subject, "report:1")), evidence)
    new, _ = bind(payload(("冒烟", subject, "report:1")), evidence)
    both, trace = bind(payload(("冒烟", subject, "report:1"), ("火花烧损", subject, "report:1")), evidence)
    assert old == new == both
    if subject == "插座":
        # Captured from the unmodified v20 implementation, not recomputed here.
        assert old == "evt2:05e0c55a9a947c87e866b99e883473a5"
    assert [row["label"] for row in trace["effective_observations"]] == ["冒烟", "火花烧损"]


def test_unknown_subject_keeps_quote_and_distinct_subjects_keep_identity():
    keys = [bind(payload(("冒烟", subject, "report:1")), catalog(text))[0]
            for subject, text in [("未确定", "外壳冒烟。"), ("未确定", "设备冒烟。"),
                                  ("插座", "插座冒烟。"), ("空调", "空调冒烟。")]]
    assert len(set(keys)) == 4


@pytest.mark.parametrize("current,old", [("冒烟", "火花烧损"), ("火花烧损", "冒烟")])
def test_historical_duplicate_uses_same_family_but_retains_raw_trace(current, old):
    _, trace = bind(payload((current, "插座", "report:1"), (old, "插座", "report:2")),
                    catalog("插座现在冒烟。昨天插座冒烟。"))
    assert trace["effective_observations"][0]["label"] == current
    assert trace["ignored_observations"][0]["label"] == old
    assert trace["ignored_observations"][0]["reason_code"] == "historical"


def test_smoke_cannot_cover_missing_gas():
    evidence = catalog("插座冒烟。厨房有燃气气味。")
    with pytest.raises(ObservationBindingError, match="Confirmed danger category was omitted"):
        bind(payload(), evidence)
    _, trace = bind(payload(("冒烟", "插座", "report:1"), ("疑似燃气气味", "未确定", "report:2")), evidence)
    assert len(trace["effective_observations"]) == 2


@pytest.mark.parametrize("text", ["外壳冒烟。", "插排没有问题，外壳冒烟。", "空调冒烟。"])
def test_device_alias_cannot_lend_subject_to_other_or_unknown_source(text):
    with pytest.raises(ObservationBindingError):
        bind(payload(), catalog(text))


@pytest.mark.parametrize("source,category,risk,cosmetic_eid,allowed", [
    ("vision", "electrical", "electric", "vision:1", True),
    ("report", "electrical", "electric", "report:1", False),
    ("vision", "other", "electric", "vision:1", False),
    ("vision", "electrical", "safety", "vision:1", False),
    ("vision", "electrical", "electric", "vision:2", False),
])
def test_vision_cosmetic_exception_retains_all_existing_gates(source, category, risk, cosmetic_eid, allowed):
    evidence = catalog("插座冒烟。", source)
    evidence[f"{source}:2"] = {"source": source, "text": "外壳完整。"}
    raw = payload(("冒烟", "插座", f"{source}:1"), ("表面剥落", "插座", cosmetic_eid), risk=risk, category=category)
    if allowed:
        _, trace = bind(raw, evidence)
        assert trace["ignored_observations"][0]["label"] == "表面剥落"
    else:
        with pytest.raises(ObservationBindingError):
            bind(raw, evidence)


def test_qwen_request_uses_canonical_additive_schema_and_current_version():
    raw = payload()
    def handler(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert {"冒烟", "火花烧损"} <= set(schema["$defs"]["Observation"]["properties"]["label"]["enum"])
        assert len(schema["properties"]) == 9
        assert request.headers["X-Prompt-Version"] == WORK_ORDER_ANALYSIS_PROMPT_VERSION
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(raw)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = QwenLLMProvider(base_url="https://operator.invalid/v1", model="synthetic",
            api_key="synthetic", timeout_seconds=1, client=client).structured_completion(
                prompt="synthetic smoke", prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION)
    assert dict(result) == raw
    schema = work_order_analysis_response_format()
    schema["json_schema"]["schema"].clear()
    assert work_order_analysis_response_format()["json_schema"]["schema"]["properties"]
