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
