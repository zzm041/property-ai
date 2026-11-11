"""PA-178: ordinary leak reports must not be rejected as subject_unsupported.

The vocabulary in `app.domain.ai.rules` is finite on purpose, but it has to cover
the wording residents actually send: seeping, gushing, backflow, standing water,
burst pipes and low pressure. These cases come from recorded evidence
(`D:\AI\review\evidence\*.md`) and from the adapter calls of work order 248, not
from invented sentences.
"""
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.domain.ai import rules
from app.service.analysis import AnalysisService


def validate(text, label, subject, *, source="report", category="water_supply"):
    raw = dict(
        category=category,
        observations=[dict(label=label, subject=subject, evidence_id=f"{source}:1")],
        severity="low",
        priority="p3",
        summary=text,
        risk="none",
        required_role="STAFF",
        required_skill="maintenance",
        confidence=1.0,
    )
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    return service._validate_payload(
        raw,
        evidence_catalog=build_evidence_catalog(
            report_text=text if source == "report" else None,
            asr_text=text if source == "asr" else None,
            vision_text=text if source == "vision" else None,
        ),
    )


# Sentences lifted from recorded evidence, never authored for the test.
REAL_REPORTS = [
    ("厨房的水管漏水了，地板已经积水，请尽快安排维修师傅上门处理。", "滴渗水", "排水接头"),
    ("厨房水管漏水，地板已经积水", "滴渗水", "阀门管道"),
    ("厨房水槽漏水", "滴渗水", "排水接头"),
    ("客厅墙面渗水", "滴渗水", "天花墙面"),
    ("墙面 渗水", "滴渗水", "天花墙面"),
    ("白色PVC排水管及弯头组件，底部管口处有一滴水珠正在下落。", "滴渗水", "排水接头"),
    ("卫生间下水管接口渗水，地面有一摊积水", "滴渗水", "排水接头"),
    ("阳台下水管漏水，楼下天花板渗水", "滴渗水", "排水接头"),
]


@pytest.mark.parametrize("text,label,subject", REAL_REPORTS)
def test_real_leak_reports_bind_without_a_subject_conflict(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert error is None
    row = mapped.event_trace["effective_observations"][0]
    assert (row["label"], row["subject"]) == (label, subject)
    assert mapped.required_duty == "MAINTENANCE"


@pytest.mark.parametrize(
    "text,label,subject",
    [
        ("墙面有明显水渍，渗漏点在管道接口", "滴渗水", "排水接头"),
        ("水管 PVC 破裂", "滴渗水", "阀门管道"),
        ("卫生间水管爆裂，水漫到客厅", "滴渗水", "阀门管道"),
        ("下水管爆管了，一直在流水", "滴渗水", "排水区域"),
        ("厨房地面冒水，地上积了一大片", "进水扩散", "楼道地面"),
        ("地面积水，污水倒灌", "进水扩散", "楼道地面"),
        ("阳台地面积水已经漫到客厅", "进水扩散", "楼道地面"),
        ("厨房水压异常，龙头出水很小", "不出水", "水龙头"),
        ("水龙头水压不足，出水很小", "不出水", "水龙头"),
    ],
)
def test_new_wording_binds_to_the_existing_vocabulary(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert error is None
    assert mapped.event_trace["effective_observations"][0]["label"] == label


@pytest.mark.parametrize(
    "label,phrase",
    [
        ("滴渗水", "渗漏"),
        ("滴渗水", "破裂"),
        ("滴渗水", "爆管"),
        ("进水扩散", "积水"),
        ("进水扩散", "漫水"),
        ("进水扩散", "返水"),
        ("进水扩散", "冒水"),
        ("进水扩散", "涌水"),
        ("进水扩散", "倒灌"),
        ("不出水", "水压异常"),
        ("不出水", "出水很小"),
    ],
)
def test_each_new_alternative_is_present_in_the_pattern(label, phrase):
    assert rules.PATTERNS[label].count("|") >= 1
    import re

    assert re.search(rules.PATTERNS[label], phrase), (label, phrase)


@pytest.mark.parametrize(
    "text,label,subject",
    [
        ("外观无明显异常，未发现漏水痕迹。", "滴渗水", "排水接头"),
        ("卫生间没有积水，地面干燥。", "进水扩散", "排水区域"),
        ("昨天水管破裂过，今天没有漏水。", "滴渗水", "排水接头"),
        ("听说水管爆了，不确定是不是这栋。", "滴渗水", "排水接头"),
    ],
)
def test_negated_or_historical_leaks_stay_rejected(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_work_order_248_evidence_binds_after_the_vocabulary_widening():
    """The photo report that failed with subject_unsupported now binds."""
    text = "白色PVC排水管及弯头组件，底部管口处有一滴水珠正在下落。"
    mapped, error = validate(text, "滴渗水", "排水接头")
    assert error is None
    row = mapped.event_trace["effective_observations"][0]
    assert row["quote"] == text


def test_lighting_report_keeps_its_own_family():
    mapped, error = validate("3号楼2单元楼道灯不亮，晚上回家看不清台阶。", "照明不亮", "照明", category="electrical")
    assert error is None
    assert mapped.event_trace["effective_observations"][0]["label"] == "照明不亮"


@pytest.mark.parametrize(
    ("text", "label", "subject"),
    [
        ("厨房水管漏水，地板已经积水", "进水扩散", "排水接头"),
        ("卫生间下水管接口渗水，地面有一摊积水", "进水扩散", "排水接头"),
        ("厨房水槽漏水，地面已经积水了", "水渍", "排水接头"),
    ],
)
def test_object_named_in_the_other_clause_still_binds(text, label, subject):
    """PA-178: one report, two clauses — the object next door still counts.

    Work order 248 failed because the label bound to 「底部管口处有一滴水珠…」
    while the object lived in the previous clause.  A location-only label clause
    may lean on the neighbouring clause for the object.
    """
    mapped, error = validate(text, label, subject)
    assert error is None
    row = mapped.event_trace["effective_observations"][0]
    assert row["effective"] is True


@pytest.mark.parametrize(
    ("text", "label", "subject"),
    [
        # The object is nowhere in the report: 墙面 only locates the symptom.
        ("墙面滴水。", "滴渗水", "水龙头"),
        # A socket is not a water object, so the leak label still conflicts.
        ("插座冒烟，地面持续进水。", "进水扩散", "插座"),
        # Burning colour on a wall keeps the strict location rule.
        ("插座冒烟，墙面发黑。", "烧焦变色", "插座"),
        # A location word never identifies a smell's source.
        ("有人吸烟，楼道里呛鼻味。", "疑似燃气气味", "厨房"),
    ],
)
def test_carry_does_not_relax_locations_or_other_families(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"
