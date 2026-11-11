"""Observable categories must preserve facts without inventing their causes."""
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text, label, subject, *, source="report", category="other", skill="maintenance"):
    raw = dict(category=category, observations=[dict(label=label, subject=subject, evidence_id=f"{source}:1")],
               severity="low", priority="p3", summary=text, risk="none", required_role="STAFF",
               required_skill=skill, confidence=1.0)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    return service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text if source == "report" else None,
        asr_text=text if source == "asr" else None, vision_text=text if source == "vision" else None))


CASES = [
    ("房门合不上。", "闭合失效", "房门", "other", "maintenance"),
    ("窗户锁扣扣不住。", "闭合失效", "窗户", "other", "maintenance"),
    ("柜门关不齐。", "闭合失效", "柜门", "other", "maintenance"),
    ("地漏排水缓慢。", "排水缓慢", "排水区域", "water_supply", "maintenance"),
    ("洗澡那块下水不利索。", "排水缓慢", "排水区域", "water_supply", "maintenance"),
    ("柜门一边往下垂。", "部件下垂", "柜门", "other", "maintenance"),
    ("有人在楼下徘徊。", "人员行为观察", "人员", "security", "security"),
    ("有个人连续拉了几辆自行车。", "人员行为观察", "人员", "security", "security"),
    ("纱窗拉到一半卡住。", "操作阻力", "窗户", "other", "maintenance"),
    ("门舌收不回去。", "操作阻力", "房门", "other", "maintenance"),
    ("楼道地上散落饭粒。", "杂物清扫", "楼道地面", "cleaning", "cleaning"),
    ("灯还是黑的。", "照明不亮", "照明", "electrical", "maintenance"),
]


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text,label,subject,category,skill", CASES)
def test_explicit_fact_is_usable_without_inventing_cause(text, label, subject, category, skill, source):
    mapped, error = validate(text, label, subject, source=source, category=category, skill=skill)
    assert error is None and mapped.dispatch_fields_applied
    row = mapped.event_trace["effective_observations"][0]
    assert (row["label"], row["subject"], row["source"]) == (label, subject, source)
    assert mapped.required_duty == {"maintenance": "MAINTENANCE", "cleaning": "CLEANING", "security": "SECURITY"}[skill]


@pytest.mark.parametrize("prefix", ["昨天", "听说", "可能", "没有"])
@pytest.mark.parametrize("text,label,subject,category,skill", CASES[:8])
def test_new_categories_do_not_turn_negation_history_or_hearsay_into_facts(prefix, text, label, subject, category, skill):
    mapped, error = validate(prefix + text, label, subject, category=category, skill=skill)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text,label,subject", [
    ("房门开关费力。", "闭合失效", "房门"),
    ("柜门下垂。", "松动", "柜门"),
    # PA-178 (t202): 地漏完全堵死 used to be rejected as a neighbouring concept.
    # Residents do mean a blocked drain by it, and rejecting the pair failed the
    # whole order, so the drain vocabulary now binds it to 排水缓慢
    # (covered positively in test_drain_blockage_vocab_202.py).
    ("排水很慢。", "排水缓慢", "排水接头"),
    ("窗户已经关闭。", "闭合失效", "窗户"),
    ("黑色灯具完好。", "照明不亮", "照明"),
    ("有人在楼下。", "人员行为观察", "人员"),
    ("自行车被偷了。", "人员行为观察", "人员"),
])
def test_neighboring_concepts_and_over_specific_subject_are_rejected(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text", ["不知道是谁但那个人正在拉动自行车。", "有人在楼下张望，我不认识他。"])
def test_unknown_identity_does_not_erase_observed_behavior(text):
    mapped, error = validate(text, "人员行为观察", "人员", category="security", skill="security")
    assert error is None and mapped.dispatch_fields_applied


def test_different_personnel_behaviors_do_not_share_event_identity():
    left, _ = validate("有人在楼下徘徊。", "人员行为观察", "人员", category="security", skill="security")
    right, _ = validate("有人正在拉动自行车。", "人员行为观察", "人员", category="security", skill="security")
    assert left.subcategory != right.subcategory


def test_cabinet_door_is_not_a_room_door():
    mapped, error = validate("柜门合不上。", "闭合失效", "房门")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_new_request_version_changes_hash_and_same_version_is_stable(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from test_ai_analysis_input import service, NOW
    from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
    from app.model.work_order import WorkOrder

    monkeypatch.setattr("app.service.analysis.context.begin_owned_transaction", lambda _: nullcontext())
    hashes = []
    for version in ["wo-analysis-v15-observable-operations", "wo-analysis-v16-observation-coverage", WORK_ORDER_ANALYSIS_PROMPT_VERSION,
                    WORK_ORDER_ANALYSIS_PROMPT_VERSION]:
        monkeypatch.setattr("app.service.analysis.context.WORK_ORDER_ANALYSIS_PROMPT_VERSION", version)
        s = service()
        s._session.info = {}
        s._work_orders = MagicMock()
        s._ai = MagicMock()
        s._work_orders.get_work_order_for_update.return_value = WorkOrder(
            id=30, house_id=4, tenant_id=2, property_id=3, reporter_user_id=8,
            status="PENDING_ANALYSIS", version=2, created_at=NOW)
        s._work_orders.get_content.return_value = SimpleNamespace(text="柜门合不上", extra_notes=None, asr_text=None)
        s._ai.list_media_with_files.return_value = ()
        s._ai.get_current_analysis_for_update.return_value = SimpleNamespace(
            id=6, analysis_version=1, status="FAILED", input_hash="old", prompt_version="old", error_code="AI.TIMEOUT")
        s._ai.get_building_id.return_value = 5
        s._ai.get_analysis_context.return_value = {}
        hashes.append(s._prepare(work_order_id=30, request_id="version-test").input_hash)
    assert len(set(hashes[:3])) == 3 and hashes[2] == hashes[3]


def test_saved_legacy_event_is_read_without_rebinding_or_rekeying(monkeypatch):
    from app.core.config import Settings
    from app.provider.llm import SUCCESS_ANALYSIS
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical analysis must not be reinterpreted")
    monkeypatch.setattr("app.service.analysis.context.bind_observations", forbidden)
    service = object.__new__(AnalysisService)
    service._settings = Settings(_env_file=None, app_env="test")
    record = SimpleNamespace(**{**SUCCESS_ANALYSIS, "subcategory": "evt2:legacy-original"})
    mapped = service._mapped_from_stored_analysis(record)
    assert mapped.subcategory == record.subcategory == "evt2:legacy-original"


@pytest.mark.parametrize("text", [
    "玄关灯，按了三回开关，还是黑的。",
    "卧室照明，按了两次开关，还是黑的。",
    "走廊灯，按开关，还是黑的。",
])
def test_omitted_lighting_subject_survives_intervening_switch_action(text):
    mapped, error = validate(text, "照明不亮", "照明", category="electrical")
    assert error is None and mapped.dispatch_fields_applied
    assert mapped.event_trace["effective_observations"][0]["subject"] == "照明"


@pytest.mark.parametrize("text", [
    "昨天玄关灯，按了三回开关，还是黑的。",
    "听说玄关灯，按了三回开关，还是黑的。",
    "玄关灯和显示屏，按了三回开关，还是黑的。",
    "玄关灯，按了三回开关，显示屏还是黑的。",
    "玄关灯，按了三回开关，不是还是黑的。",
    "玄关灯。按了三回开关，还是黑的。",
])
def test_lighting_ellipsis_does_not_cross_unknown_object_history_or_sentence(text):
    mapped, error = validate(text, "照明不亮", "照明", category="electrical")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text", ["不是有人在楼下徘徊。", "并非有人在楼下徘徊。", "未发现有人在楼下徘徊。"])
def test_denied_personnel_behavior_cannot_dispatch(text):
    mapped, error = validate(text, "人员行为观察", "人员", category="security", skill="security")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_personnel_observation_cannot_be_sent_to_maintenance():
    mapped, error = validate("有人在楼下徘徊。", "人员行为观察", "人员")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_restored_light_is_not_a_current_dark_light():
    mapped, error = validate("灯还是黑的但已经正常亮起来了。", "照明不亮", "照明", category="electrical")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("text", ["灯不亮但还没有恢复照明。", "灯还是黑的但没有正常亮起来。"])
def test_light_that_has_not_recovered_remains_a_current_fault(text):
    mapped, error = validate(text, "照明不亮", "照明", category="electrical")
    assert error is None and mapped.dispatch_fields_applied


def test_existing_drain_leak_subject_continuation_remains_compatible():
    mapped, error = validate("排水处看着正常，现在滴水。", "滴渗水", "排水接头", category="water_supply")
    assert error is None
    assert mapped.event_trace["effective_observations"][0]["subject"] == "排水接头"
