from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.audit import AuditAction, AuditResourceType
from app.domain.iam import OrganizationScope, ScopeType
from app.domain.observed_event import (
    ObservedAnalysisOutput,
    bind_observations,
    build_evidence_catalog,
    prompt_evidence_catalog,
)
from app.service.analysis import AnalysisService
from app.service.audit import AuditService


def payload(text="厨房水龙头关紧后还在滴水", label="滴渗水", subject="水龙头", evidence_id="report:1"):
    return {
        "category": "water_supply",
        "observations": [{"label": label, "subject": subject, "evidence_id": evidence_id}],
        "severity": "low",
        "priority": "p4",
        "summary": text,
        "risk": "none",
        "required_role": "STAFF",
        "required_skill": "maintenance",
        "confidence": .9,
    }


def catalog(*, report=None, asr=None, vision=None):
    return build_evidence_catalog(report_text=report, asr_text=asr, vision_text=vision)


def bind(value, *, report=None, asr=None, vision=None):
    return bind_observations(ObservedAnalysisOutput.model_validate(value), catalog(report=report, asr=asr, vision=vision))


def saved_case(relative_path, case_id):
    record = json.loads((Path(__file__).resolve().parents[3] / relative_path).read_text(encoding="utf-8"))
    return next(row for row in record["results"] if row["id"] == case_id)


def test_server_builds_stable_current_source_ids_without_history():
    evidence = catalog(
        report="水龙头滴水。插座没电。",
        asr="住户补充听到异响。",
        vision='[{"image_index":1,"description":"墙面有水渍","error_code":null}]',
    )
    assert list(evidence) == ["report:1", "report:2", "asr:1", "vision:1"]
    assert evidence["report:1"]["text"] == "水龙头滴水。"
    assert evidence["vision:1"]["source"] == "vision"
    assert prompt_evidence_catalog(evidence)[0] == {
        "id": "report:1", "source": "report", "text": "水龙头滴水。"
    }


def test_evidence_id_resolves_original_text_and_key_without_model_quote():
    value = payload()
    key, trace = bind(value, report=value["summary"])
    assert key.startswith("evt2:") and len(key) <= 64
    assert trace["raw_model_output"] == value
    assert trace["observations"][0] == {
        "label": "滴渗水", "subject": "水龙头", "evidence_id": "report:1",
        "source": "report", "quote": value["summary"], "effective": True,
        "span": [0, len(value["summary"])],
    }


def test_unknown_or_cross_source_evidence_id_is_rejected():
    with pytest.raises(ValueError, match="not in the current catalog"):
        bind(payload(evidence_id="vision:1"), report="厨房水龙头滴水")
    bad = payload(evidence_id="report:1")
    with pytest.raises(ValueError, match="catalog is invalid"):
        bind_observations(
            ObservedAnalysisOutput.model_validate(bad),
            {"report:1": {"source": "vision", "text": "厨房水龙头滴水"}},
        )


def test_same_phenomenon_and_subject_merge_but_different_subject_does_not():
    first = bind(payload(), report="厨房水龙头关紧后还在滴水")[0]
    second = bind(payload("水龙头一直滴水"), report="水龙头一直滴水")[0]
    third_value = payload("洗手盆接头有渗水", subject="排水接头")
    third = bind(third_value, report=third_value["summary"])[0]
    assert first == second and first != third


@pytest.mark.parametrize("text", [
    "水龙头没有漏水", "上次水龙头滴水", "我怀疑水龙头滴水",
    "邻居说水龙头滴水", "一人说水龙头滴水",
])
def test_matching_words_do_not_prove_negation_history_guess_or_hearsay(text):
    with pytest.raises(ValueError):
        bind(payload(text), report=text)


@pytest.mark.parametrize("text", ["插座火花没有出现", "插座火花并没有出现", "插座火花从未出现"])
def test_postposed_negation_cannot_share_positive_key(text):
    with pytest.raises(ValueError, match="Postposed"):
        bind(payload(text, label="火花烧损", subject="插座"), report=text)


def test_conflicting_current_report_can_only_be_unconfirmed():
    text = "一人说水龙头不断流出，另一人说完全不出水，无法判断真伪。"
    confirmed = payload(text, label="不出水")
    with pytest.raises(ValueError, match="not confirmed"):
        bind(confirmed, report=text)
    waiting = payload(text, label="现象待核实", subject="未确定")
    waiting["category"] = "other"
    waiting["required_skill"] = "other"
    waiting["confidence"] = .3
    key, trace = bind(waiting, report=text)
    assert key.startswith("evt2:") and trace["observations"][0]["quote"] == text


def test_vision_evidence_is_derived_and_not_user_confirmation():
    vision = '[{"image_index":1,"description":"天花墙面可见水渍","error_code":null}]'
    value = payload("天花墙面可见水渍", label="水渍", subject="天花墙面", evidence_id="vision:1")
    _, trace = bind(value, report="不知道是否漏水", vision=vision)
    assert trace["observations"][0]["source"] == "vision"
    assert trace["observations"][0]["quote"] == "天花墙面可见水渍"


def test_all_faults_contribute_to_key_and_order_is_irrelevant():
    text = "厨房水龙头关紧后还在滴水。卧室插座不能供电。"
    value = payload()
    value["observations"].append({"label": "不能供电", "subject": "插座", "evidence_id": "report:2"})
    reversed_value = {**value, "observations": list(reversed(value["observations"]))}
    assert bind(value, report=text)[0] == bind(reversed_value, report=text)[0]
    assert bind(value, report=text)[0] != bind(payload(), report=text)[0]


def test_normal_and_unconfirmed_context_remain_traceable_but_not_in_effective_key():
    report = "厨房插座没有电，其他房间照明正常。我怀疑是线路老化。"
    base = payload("厨房插座没有电", label="不能供电", subject="插座", evidence_id="report:1")
    with_context = {**base, "observations": [
        *base["observations"],
        {"label": "外观无明显异常", "subject": "照明", "evidence_id": "report:1"},
        {"label": "现象待核实", "subject": "未确定", "evidence_id": "report:2"},
    ]}
    base_key, _ = bind(base, report=report)
    contextual_key, trace = bind(with_context, report=report)
    assert contextual_key == base_key
    assert len(trace["effective_observations"]) == 1
    assert len(trace["informational_observations"]) == 2
    assert trace["raw_model_output"] == with_context


def test_invalid_redundant_informational_observation_does_not_reject_core_fault():
    report = "空调出风但不凉，旁边照明正常。"
    value = payload(report, label="不制冷", subject="空调")
    value.update(category="hvac", required_skill="maintenance")
    value["observations"].append({
        "label": "外观无明显异常", "subject": "空调", "evidence_id": "report:1",
    })
    _, trace = bind(value, report=report)
    assert [row["label"] for row in trace["effective_observations"]] == ["不制冷"]
    assert trace["ignored_observations"] == [{
        "label": "外观无明显异常",
        "subject": "空调",
        "evidence_id": "report:1",
        "source": "report",
        "quote": report,
        "reason": "Subject not supported by the selected evidence",
        "reason_code": "subject_unsupported",
        "span": [0, len(report)],
        "disposition": "NOT_TRUSTED_REDUNDANT_OBSERVATION",
        "ignored_reason": "informational observation is not needed for the accepted current fault",
    }]
    assert trace["raw_model_output"] == value


def test_historical_duplicate_can_be_ignored_only_when_current_identity_is_accepted():
    report = "昨晚洗手盆下面漏过，关阀后停了。今天洗手盆下面又开始滴水。"
    value = payload(report, subject="排水接头", evidence_id="report:2")
    value["observations"].append({
        "label": "滴渗水", "subject": "排水接头", "evidence_id": "report:1",
    })
    _, trace = bind(value, report=report)
    assert len(trace["effective_observations"]) == 1
    assert trace["ignored_observations"][0]["ignored_reason"].startswith("unconfirmed or historical")


def test_unrelated_historical_or_dangerous_observation_cannot_be_silently_ignored():
    report = "昨晚插座出现火花。今天洗手盆下面开始滴水。"
    value = payload(report, subject="排水接头", evidence_id="report:2")
    value["observations"].append({
        "label": "火花烧损", "subject": "插座", "evidence_id": "report:1",
    })
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(value, report=report)


def test_same_subject_current_positive_and_negative_are_not_treated_as_history_redundancy():
    report = "插座正在冒烟。插座没有冒烟。"
    value = payload(report, label="火花烧损", subject="插座", evidence_id="report:1")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    value["observations"].append({
        "label": "火花烧损", "subject": "插座", "evidence_id": "report:2",
    })
    with pytest.raises(ValueError, match="Negated observation"):
        bind(value, report=report)


def test_same_subject_current_guess_is_not_treated_as_history_redundancy():
    report = "插座正在冒烟。住户猜插座可能冒烟。"
    value = payload(report, label="火花烧损", subject="插座", evidence_id="report:1")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    value["observations"].append({
        "label": "火花烧损", "subject": "插座", "evidence_id": "report:2",
    })
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(value, report=report)


def test_unknown_catalog_subject_cannot_borrow_an_unrelated_unique_subject():
    report = "插座没有发黑，电表发黑。"
    value = payload(report, label="烧焦变色", subject="插座")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(value, report=report)


def test_invalid_information_cannot_be_dropped_without_a_valid_effective_fault():
    text = "照明正常，没有冒烟。"
    value = payload(text, label="外观无明显异常", subject="空调")
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(value, report=text)


def test_unsupported_cosmetic_vision_extra_does_not_hide_supported_electrical_danger():
    vision = "插座一个插孔有明显黑色烧灼痕迹，周围墙面有粗糙修补痕迹。"
    value = payload(vision, label="烧焦变色", subject="插座", evidence_id="vision:1")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    value["observations"].append({
        "label": "表面剥落", "subject": "天花墙面", "evidence_id": "vision:1",
    })
    _, trace = bind(value, vision=vision)
    assert [row["label"] for row in trace["effective_observations"]] == ["烧焦变色"]
    assert trace["ignored_observations"][0]["disposition"] == "NOT_TRUSTED_REDUNDANT_OBSERVATION"
    assert "noncritical cosmetic" in trace["ignored_observations"][0]["ignored_reason"]
    assert trace["raw_model_output"] == value


@pytest.mark.parametrize("category,risk", [("other", "none"), ("electrical", "none")])
def test_unsupported_cosmetic_vision_extra_cannot_be_dropped_without_independent_danger(category, risk):
    vision = "蓝色阀门平放在地面，表面有反光和正常金属连接件。"
    value = payload(vision, label="表面剥落", subject="阀门管道", evidence_id="vision:1")
    value.update(category=category, required_skill="maintenance", risk=risk)
    with pytest.raises(ValueError, match="Evidence does not support"):
        bind(value, vision=vision)


def test_cosmetic_extra_from_another_source_cannot_be_dropped_from_vision_danger():
    value = payload("墙面有粗糙修补痕迹。", label="表面剥落", subject="天花墙面")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    value["observations"].insert(0, {
        "label": "烧焦变色", "subject": "插座", "evidence_id": "vision:1",
    })
    with pytest.raises(ValueError, match="Evidence does not support"):
        bind(value, report="墙面有粗糙修补痕迹。", vision="插座有明显黑色烧灼痕迹。")


def test_saved_q03_outputs_accept_core_fault_and_record_ignored_redundancy():
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")
    fixed = "reports/real-ai-integration-027/diagnostic-r3-9b-v11/candidate-03/fixed.json"
    for case_id, ignored_count in (("GOLD-004", 1), ("GOLD-022", 1)):
        row = saved_case(fixed, case_id)
        evidence = catalog(report=row["input"]["report"])
        mapped, error = service._validate_payload(row["raw_payload"], evidence_catalog=evidence)
        assert error is None and mapped is not None
        assert len(mapped.event_trace["effective_observations"]) == 1
        assert len(mapped.event_trace["ignored_observations"]) == ignored_count
        assert mapped.event_trace["raw_model_output"] == row["raw_payload"]


@pytest.mark.parametrize(("relative_path", "case_id"), [
    ("reports/real-ai-integration-027/diagnostic-r3-9b-v11/candidate-03/fixed.json", "PHOTO-002"),
    ("reports/real-ai-integration-027/diagnostic-r3-9b-v11/candidate-03/fixed.json", "GOLD-012"),
    ("reports/real-ai-integration-027/diagnostic-r3-9b-v11/candidate-03/novel.json", "NOVEL-004"),
])
def test_saved_danger_conflict_and_cross_object_failures_remain_rejected(relative_path, case_id):
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")
    row = saved_case(relative_path, case_id)
    report = row["input"].get("report") or row["input"].get("paired_report")
    evidence = catalog(report=report)
    mapped, error = service._validate_payload(row["raw_payload"], evidence_catalog=evidence)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize(("text", "label", "subject"), [
    ("空调出风但不凉，没有漏水，也没有异味。", "外观无明显异常", "未确定"),
    ("卧室没有冒烟或火花。", "现象待核实", "未确定"),
    ("用户认为插座完全没有烧焦。", "现象待核实", "未确定"),
    ("浴室水龙头一点水都不出。", "不出水", "水龙头"),
    ("客厅窗边墙皮鼓起并掉了一小块。", "表面剥落", "天花墙面"),
    ("电梯厅有一袋废纸需要清理。", "杂物清扫", "楼道地面"),
])
def test_common_chinese_equivalents_bind_without_case_specific_rules(text, label, subject):
    value = payload(text, label=label, subject=subject)
    _, trace = bind(value, report=text)
    assert trace["observations"][0]["quote"] == text


def test_last_night_fault_cannot_become_a_current_effective_event():
    text = "昨晚洗手盆下面漏过，关阀后停了。"
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(payload(text, subject="排水接头"), report=text)


def test_current_clause_is_not_rejected_by_a_separate_historical_clause():
    text = "昨晚玄关灯闪过两次，今天开灯时开关面板出现火花。"
    value = payload(text, label="火花烧损", subject="未确定")
    _, trace = bind(value, report=text)
    assert trace["effective_observations"][0]["label"] == "火花烧损"


def test_historical_clause_still_cannot_support_current_event_when_later_clause_is_normal():
    text = "昨晚插座出现火花，今天外观正常。"
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(payload(text, label="火花烧损", subject="插座"), report=text)


def test_positive_match_for_another_subject_cannot_rescue_negated_requested_subject():
    text = "插座没有发黑，墙面发黑。"
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(payload(text, label="烧焦变色", subject="插座"), report=text)


def test_leading_hearsay_modifier_applies_to_following_comma_clause():
    text = "据说，插座正在冒烟。"
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(payload(text, label="火花烧损", subject="插座"), report=text)


@pytest.mark.parametrize("text", ["我怀疑，插座冒烟。", "可能，插座冒烟。"])
def test_leading_uncertainty_modifier_applies_to_following_comma_clause(text):
    with pytest.raises(ValueError, match="not confirmed current"):
        bind(payload(text, label="火花烧损", subject="插座"), report=text)


def test_uncertain_cause_does_not_hide_separate_current_observation():
    text = "我怀疑线路老化，插座正在冒烟。"
    _, trace = bind(payload(text, label="火花烧损", subject="插座"), report=text)
    assert trace["effective_observations"][0]["label"] == "火花烧损"


def test_unknown_subject_keeps_exact_evidence_out_of_wildcard_key():
    left = payload("左侧外壳已经发黑", label="烧焦变色", subject="未确定")
    right = payload("右侧外壳已经发黑", label="烧焦变色", subject="未确定")
    left["risk"] = right["risk"] = "electric"
    assert bind(left, report=left["summary"])[0] != bind(right, report=right["summary"])[0]


def test_subject_must_be_supported_by_selected_server_evidence():
    value = payload("外壳已经发黑", label="烧焦变色", subject="插座")
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(value, report=value["summary"])


def test_supported_faults_keep_risk_while_overattached_subject_is_reduced_explicitly():
    text = "插座正在连续冒火花，塑料外壳已经发黑，还有明显焦味。"
    value = payload(text, label="火花烧损", subject="插座")
    value.update(category="electrical", required_skill="maintenance", risk="electric",
                 severity="high", priority="p1")
    value["observations"].extend([
        {"label": "烧焦变色", "subject": "插座", "evidence_id": "report:1"},
        {"label": "焦煳气味", "subject": "插座", "evidence_id": "report:1"},
    ])

    _, trace = bind(value, report=text)

    assert [(row["label"], row["subject"]) for row in trace["effective_observations"]] == [
        ("火花烧损", "插座"),
        ("烧焦变色", "未确定"),
        ("焦煳气味", "未确定"),
    ]
    assert len(trace["adjusted_observations"]) == 2
    assert {row["raw_subject"] for row in trace["adjusted_observations"]} == {"插座"}
    assert trace["raw_model_output"] == value


def test_subject_reduction_does_not_accept_a_standalone_cross_object_claim():
    text = "插座没有发黑，电表发黑。"
    value = payload(text, label="烧焦变色", subject="插座")
    value.update(category="electrical", required_skill="maintenance", risk="electric")
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(value, report=text)


def test_saved_qwen38_candidate3_danger_keeps_raw_subject_error_and_effective_risk():
    row = saved_case(
        "reports/real-ai-integration-027/diagnostic-r3-27b/candidate-03/screen.json",
        "GOLD-007",
    )
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")

    mapped, error = service._validate_payload(
        row["raw_payload"], evidence_catalog=catalog(report=row["input"]["report"]),
    )

    assert error is None and mapped is not None
    assert mapped.risk.value == "electric"
    assert [item["subject"] for item in mapped.event_trace["adjusted_observations"]] == [
        "未确定", "未确定",
    ]
    assert all(item["raw_subject"] == "插座" for item in mapped.event_trace["adjusted_observations"])
    assert mapped.event_trace["raw_model_output"] == row["raw_payload"]


def test_low_confidence_component_identification_reduces_subject_without_creating_fault():
    vision = "图像中可见一个蓝色的阀门，带有手柄和法兰连接件。阀门表面有反光，未见足以确认故障的可见证据。"
    value = payload(vision, label="现象待核实", subject="阀门管道", evidence_id="vision:1")
    value.update(category="other", required_skill="other", confidence=.3)

    _, trace = bind(value, vision=vision)

    assert trace["effective_observations"] == []
    assert trace["informational_observations"][0]["subject"] == "未确定"
    assert trace["adjusted_observations"][0]["raw_subject"] == "阀门管道"
    assert trace["raw_model_output"] == value


def test_informational_subject_reduction_requires_non_dispatch_contract():
    vision = "图像中可见一个蓝色阀门。未见足以确认故障的可见证据。"
    value = payload(vision, label="现象待核实", subject="阀门管道", evidence_id="vision:1")
    for overrides in ({"category": "water_supply", "required_skill": "maintenance", "confidence": .3},):
        with pytest.raises(ValueError, match="Subject not supported"):
            bind({**value, **overrides}, vision=vision)

    _, trace = bind({**value, "category": "other", "required_skill": "other", "confidence": .95}, vision=vision)
    assert trace["effective_observations"] == []


def test_saved_qwen38_candidate4_normal_valve_is_valid_non_effective_clarification():
    row = saved_case(
        "reports/real-ai-integration-027/diagnostic-r3-27b/candidate-04/screen.json",
        "PHOTO-004",
    )
    vision_result = row["stages"][0]["provider_result"][0]
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")

    mapped, error = service._validate_payload(
        row["raw_payload"], evidence_catalog=catalog(report=row["input"].get("paired_report"), vision=vision_result),
    )

    assert error is None and mapped is not None and mapped.low_confidence
    assert mapped.event_trace["effective_observations"] == []
    assert mapped.event_trace["raw_model_output"] == row["raw_payload"]


def test_frequency_clause_carries_the_unique_immediately_prior_subject():
    text = "厨房洗手盆下方的排水接头已经裂开，现在每隔几秒滴一滴水。"
    value = payload(text, subject="排水接头")
    _, trace = bind(value, report=text)
    assert trace["effective_observations"][0]["subject"] == "排水接头"


def test_kitchen_sink_location_supports_drain_connection_for_current_leak():
    text = "厨房水槽下方持续滴水，地柜内有明显水渍，请安排普通维修处理。"
    _, trace = bind(payload(text, subject="排水接头"), report=text)
    assert trace["effective_observations"][0]["subject"] == "排水接头"


def test_valve_operating_resistance_does_not_support_invented_looseness():
    text = "卫生间阀门开关费力且有少量渗水。"
    value = payload(text, label="松动", subject="阀门管道")
    with pytest.raises(ValueError, match="Evidence does not support observable label"):
        bind(value, report=text)


def test_explicit_valve_looseness_remains_supported():
    text = "卫生间阀门已经松动，并有少量渗水。"
    _, trace = bind(payload(text, label="松动", subject="阀门管道"), report=text)
    assert trace["effective_observations"][0]["label"] == "松动"


@pytest.mark.parametrize(("text", "label", "subject"), [
    ("插座刚才有一缕烟，现在没有了。", "现象待核实", "插座"),
    ("厨房进水软管裂开，水一直喷出。", "滴渗水", "阀门管道"),
    ("卫生间天花板有一片新水渍，还在缓慢滴水。", "滴渗水", "天花墙面"),
    ("关于此刻同一个厨房水龙头，一人说水不断流出，另一人说完全不出水。", "现象待核实", "水龙头"),
    ("阀体表面蓝色涂层明显磨损和掉漆，露出金属底色。", "表面剥落", "阀门管道"),
])
def test_equivalent_observation_and_local_unique_subject_carry(text, label, subject):
    _, trace = bind(payload(text, label=label, subject=subject), report=text)
    assert trace["observations"][0]["subject"] == subject


def test_subject_carry_does_not_cross_a_sentence_boundary():
    text = "插座刚才有一缕烟。现在没有了。"
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(payload(text, label="现象待核实", subject="插座", evidence_id="report:2"), report=text)


def test_label_specific_subject_carry_rejects_a_negated_prior_subject():
    text = "不是空调，出风但不凉。"
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(payload(text, label="不制冷", subject="空调"), report=text)


@pytest.mark.parametrize("text", [
    "我怀疑天花板有问题，还在缓慢滴水。",
    "洗手盆下面有水，电表还在缓慢滴水。",
])
def test_subject_carry_rejects_uncertain_or_competing_prior_subject(text):
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(payload(text, label="滴渗水", subject="天花墙面"), report=text)


def test_subject_carry_rejects_the_same_negated_prior_subject():
    text = "不是水龙头，正在滴水。"
    with pytest.raises(ValueError, match="Subject not supported"):
        bind(payload(text, label="滴渗水", subject="水龙头"), report=text)


def test_visible_pooled_water_uses_existing_water_stain_event():
    value = payload("厨房地面已经积水并流向客厅", label="水渍", subject="楼道地面")
    value.update(risk="flooding", severity="high", priority="p1")
    assert bind(value, report=value["summary"])[0].startswith("evt2:")


def test_old_model_authored_quote_protocol_is_not_silently_rewritten():
    record = json.loads((Path(__file__).resolve().parents[3] / "reports/real-ai-integration-027/diagnostic-r3-9b/candidate-02/fixed.json").read_text(encoding="utf-8"))
    original = next(row for row in record["results"] if row["id"] == "PHOTO-002")["raw_payload"]
    with pytest.raises(ValidationError):
        ObservedAnalysisOutput.model_validate(original)
    assert original["observations"][0]["quote"] == "照片外观没有发黑"


def test_real_provider_requires_evidence_id_and_preserves_effective_key():
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")
    value = payload()
    evidence = catalog(report=value["summary"])
    mapped, error = service._validate_payload(value, evidence_catalog=evidence)
    assert error is None and mapped.required_duty == "MAINTENANCE"
    assert mapped.event_trace["raw_model_output"] == value
    legacy = {key: item for key, item in value.items() if key != "observations"}
    legacy["subcategory"] = "outlet_spark"
    assert service._validate_payload(legacy)[0] is None
    row = SimpleNamespace()
    service._fill_analysis(row, mapped)
    assert row.subcategory == mapped.subcategory
    assert service._mapped_from_stored_analysis(row).subcategory == mapped.subcategory


def test_scoped_audit_retains_raw_fields_and_resolved_evidence():
    service = AnalysisService.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, llm_provider="production")
    service._audit = Mock()
    value = payload()
    mapped, _ = service._validate_payload(value, evidence_catalog=catalog(report=value["summary"]))
    service._append_success_audit(
        SimpleNamespace(id=5, tenant_id=1, property_id=2),
        SimpleNamespace(id=6, status="COMPLETED"), "unit", mapped,
    )
    details = service._audit.append.call_args.kwargs["details"]
    assert details["event_evidence"]["raw_model_output"] == value
    assert details["event_evidence"]["observations"][0]["quote"] == value["summary"]
    AuditService._validate_details(
        action=AuditAction.WORK_ORDER_AI_ANALYSIS_COMPLETED,
        resource_type=AuditResourceType.WORK_ORDER_ANALYSIS,
        scope=OrganizationScope(ScopeType.PROPERTY, tenant_id=1, property_id=2),
        details=details,
    )
