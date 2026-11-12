"""PA-209 (t209): one mislabelled observation must not erase the analysis.

WO597 showed the failure mode: the model answered "排水缓慢" for a sentence that
only says "渗水", the regex for that label matched nothing, and because a single
rejected row aborted the whole payload the work order ended FAILED even though
two other observations were supported by the very same evidence.

The contract below is the narrow one: only the plain "no evidence for this
label" rejection is demoted to `unbound_observations`.  Negative, historical and
uncertain phrasing, subject conflicts and unknown evidence keep failing the
payload, because those are the guards that stop a fabricated report.
"""
import pytest

from app.domain.ai import ObservedAnalysisOutput, bind_observations, build_evidence_catalog


def output(*observations, category="water_supply", risk="none", skill="maintenance"):
    return ObservedAnalysisOutput.model_validate(dict(
        category=category,
        observations=[dict(label=label, subject=subject, evidence_id=evidence_id)
                      for label, subject, evidence_id in observations],
        severity="low",
        priority="p3",
        summary="synthetic",
        risk=risk,
        required_role="STAFF",
        required_skill=skill,
        confidence=0.9,
    ))


REPORT = "厨房水槽下方的排水管接头渗水，地上有积水。"
VISION = "白色PVC排水管及弯头组件，底部管口处有一滴水珠正在下落。"


def catalog(report=REPORT, vision=VISION):
    return build_evidence_catalog(report_text=report, asr_text=None, vision_text=vision)


def test_one_mislabelled_observation_no_longer_fails_the_payload():
    # Exactly WO597's answer: 排水缓慢 is unsupported by the report sentence,
    # 进水扩散 and 滴渗水 are.
    key, trace = bind_observations(output(
        ("排水缓慢", "排水接头", "report:1"),
        ("进水扩散", "排水区域", "report:1"),
        ("滴渗水", "排水接头", "vision:1"),
    ), catalog())
    assert key.startswith("evt2:")
    assert [row["label"] for row in trace["effective_observations"]] == ["进水扩散", "滴渗水"]
    assert [row["label"] for row in trace["unbound_observations"]] == ["排水缓慢"]
    unbound = trace["unbound_observations"][0]
    assert unbound["reason_code"] == "label_not_matched"
    assert unbound["disposition"] == "NOT_BOUND_TO_EVIDENCE"
    # The original label survives verbatim: no renaming to 滴渗水.
    assert unbound["label"] == "排水缓慢" and unbound["subject"] == "排水接头"
    assert unbound["evidence_id"] == "report:1"
    assert unbound["quote"] == REPORT


def test_unbound_rows_do_not_leak_into_the_effective_set():
    _, trace = bind_observations(output(
        ("排水缓慢", "排水接头", "report:1"),
        ("进水扩散", "排水区域", "report:1"),
    ), catalog())
    assert "排水缓慢" not in [row["label"] for row in trace["effective_observations"]]
    assert "排水缓慢" not in [row["label"] for row in trace["ignored_observations"]]
    assert len(trace["unbound_observations"]) == 1


def test_every_observation_unsupported_still_fails_with_the_original_reason():
    # No row matches "排水缓慢" (the sentence never says 堵/慢/流不下去), so the
    # unbound bucket must not turn an evidence-free answer into an analysis.
    with pytest.raises(ValueError) as caught:
        bind_observations(output(
            ("排水缓慢", "排水接头", "report:1"),
            ("排水缓慢", "排水区域", "report:1"),
            ("操作阻力", "房门", "vision:1"),
        ), catalog(report="厨房水槽下方的排水管接头渗水。", vision="卫生间门口地面有一摊积水。"))
    assert getattr(caught.value, "reason_code", None) == "label_not_matched"


def test_subject_conflict_is_not_rescued_by_the_unbound_bucket():
    # 排水接头 requires 接头 in the clause; the sentence only says 水槽.
    with pytest.raises(ValueError) as caught:
        bind_observations(output(
            ("排水缓慢", "排水接头", "report:1"),
            ("进水扩散", "排水区域", "report:1"),
        ), catalog(report="厨房水槽堵了，水流不下去，地上有积水。"))
    assert getattr(caught.value, "reason_code", None) == "subject_conflict"


@pytest.mark.parametrize("text,label,reason", [
    ("水龙头没有滴水。", "滴渗水", "prefixed_negative"),
    ("水龙头滴水并没有发生。", "滴渗水", "postposed_negative"),
    ("昨天水龙头滴水。", "滴渗水", "historical"),
    ("听说水龙头滴水。", "滴渗水", "uncertain"),
])
def test_negated_or_historical_phrasing_still_fails_even_with_a_supported_row(text, label, reason):
    with pytest.raises(ValueError) as caught:
        bind_observations(output(
            (label, "水龙头", "report:1"),
            ("进水扩散", "排水区域", "report:2"),
        ), build_evidence_catalog(report_text=text + "地上有积水。", asr_text=None, vision_text=None))
    assert getattr(caught.value, "reason_code", None) == reason


def test_a_mislabel_from_another_fault_family_stays_fatal():
    # The tolerance is scoped to neighbouring phenomena of the same family: a
    # wiring question cannot borrow credibility from a supported smoke reading.
    with pytest.raises(ValueError) as caught:
        bind_observations(output(
            ("排水缓慢", "排水接头", "report:1"),
            ("冒烟", "插座", "report:1"),
        ), build_evidence_catalog(report_text="插座冒烟。", asr_text=None, vision_text=None))
    assert getattr(caught.value, "reason_code", None) == "label_not_matched"
