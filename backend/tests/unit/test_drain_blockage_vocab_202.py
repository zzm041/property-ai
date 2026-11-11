"""PA-178: residents report a blocked drain as 堵/不通/流不下去, not only as 排水慢.

The phrases below are lifted from real delivery-check work orders (WO 587/588/589)
so the vocabulary is pinned to what residents actually type, not to invented text.
"""
import pytest

from app.domain.ai import ObservedAnalysisOutput, bind_observations, build_evidence_catalog


def raw(label="排水缓慢", subject="排水区域", evidence_id="report:1"):
    return dict(
        category="water_supply",
        observations=[dict(label=label, subject=subject, evidence_id=evidence_id)],
        severity="low",
        priority="p3",
        summary="synthetic",
        risk="none",
        required_role="STAFF",
        required_skill="maintenance",
        confidence=0.9,
    )


def bind(text, label="排水缓慢", subject="排水区域"):
    catalog = build_evidence_catalog(report_text=text, asr_text=None, vision_text=None)
    return bind_observations(ObservedAnalysisOutput.model_validate(raw(label, subject)), catalog)


@pytest.mark.parametrize("text,subject", [
    ("厨房水槽堵了，水流不下去，需要用工具疏通一下。", "排水区域"),
    ("厨房水槽堵了，水流不下去，需要用工具疏通一下，麻烦师傅尽快来看看。", "排水区域"),
    ("洗菜盆下水很慢，水放下去要很久。", "排水区域"),
    ("卫生间马桶堵塞了，冲不下去水。", "排水区域"),
    ("地漏堵住了，洗澡水漫到脚面。", "排水区域"),
    ("下水道不通，需要疏通一下管道。", "排水区域"),
    ("地漏完全堵死。", "排水区域"),
])
def test_blocked_drain_phrases_bind_to_the_drain_area(text, subject):
    normalized, observations = bind(text, subject=subject)
    assert normalized
    assert [row["label"] for row in observations["observations"]] == ["排水缓慢"]
    assert observations["observations"][0]["subject"] == subject


@pytest.mark.parametrize("text,reason", [
    # A blockage report from last time is history, never current evidence.
    ("上次厨房水槽也堵过，后来自己通了。", "historical"),
    # Unrelated obstructions must not be read as a blocked drain.
    ("消防通道堆放了纸箱和杂物，需要清理。", "label_not_matched"),
    ("3号楼2单元楼道灯不亮，晚上回家看不清台阶。", "label_not_matched"),
    ("入户门合不上，需要调整门锁。", "label_not_matched"),
])
def test_widened_drain_vocabulary_keeps_rejecting_what_it_should(text, reason):
    with pytest.raises(ValueError) as caught:
        bind(text)
    assert getattr(caught.value, "reason_code", None) == reason


def test_drain_area_accepts_sink_nouns_without_stealing_the_pipe_joint():
    # 排水接头 keeps its own 接头 requirement; a bare sink phrase stays 排水区域.
    assert bind("厨房水槽堵了，水流不下去。", subject="排水区域")[0]
    with pytest.raises(ValueError) as caught:
        bind("厨房水槽堵了，水流不下去。", subject="排水接头")
    assert getattr(caught.value, "reason_code", None) == "subject_conflict"
