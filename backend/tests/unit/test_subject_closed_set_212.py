"""PA-212 (t212): the subject is a closed set, and an unsupported one is demoted.

Two field reports died as `subject_unsupported`:

    卫生间地漏不下水，洗澡的时候积水     -> (排水缓慢, 排水区域)
    厨房水槽堵了，水流不下去             -> (排水接头, 排水区域)

The diagnosis is not "the vocabulary is too small".  `Subject` is already a
closed enum and it reaches the model inside the prompt schema; the failure was
the *clause guard*: it wanted the sentence to name the abstract alias it
received, and the sentence named a fixture instead (地漏 / 水槽 / 弯头).  Two
narrow halves fix it:

* the object vocabulary behind the shipped subjects learns the fixture words
  residents actually use (地漏, 水槽, 排水口, 三通, 角阀, 阳台排水 ...), and
* an *unsupported subject* is kept verbatim in `unbound_observations` under the
  PA-209 guard instead of killing the payload - only when the same evidence
  already proves a phenomenon of the same fault family.

The enum itself must not grow: the delivery adapter pins
`work_order_analysis_response_format()` byte-for-byte from its own snapshot, so
a new member changes the response_format and every call is rejected with
`QWEN_SCHEMA_MISMATCH`.  `test_the_subject_enum_stays_the_shipped_closed_set`
is the tripwire for that.

What stays fatal, on purpose: a subject the chosen clause really does name
differently (`subject_conflict` - the fabrication guard), negated or conditional
phrasing, an object claimed against evidence that proves nothing, and a payload
where nothing at all binds.
"""
import re
import typing

import pytest

from app.domain.ai import (
    ObservedAnalysisOutput,
    bind_observations,
    build_evidence_catalog,
    work_order_analysis_response_format,
)
from app.domain.ai.models import Subject
from app.domain.ai.rules import SUBJECT_PATTERNS

# The fixtures residents name, each in the sentence the field reported it.
FIELD_OBJECTS = {
    "地漏": "卫生间地漏不下水，洗澡的时候积水。",
    "水槽": "厨房水槽堵了，水流不下去。",
    "洗手盆": "卫生间洗手盆下水慢，水放半天才下去。",
    "马桶": "主卧马桶堵了，水冲不下去。",
    "管道": "卫生间暗管接口渗水，墙根有水印。",
    "接头": "厨房水槽下面的弯头渗水。",
    "阀门": "卫生间角阀漏水，关不紧。",
    "排水口": "阳台落水口返水，地上有积水。",
    "阳台排水": "阳台排水不通，地面有积水。",
    "洗衣机排水口": "洗衣机出水接口渗水，地上一摊水。",
}

SHIPPED_SUBJECTS = (
    "水龙头", "排水接头", "天花墙面", "插座", "照明", "空调", "扶手",
    "楼道地面", "房门", "阀门管道", "窗户", "柜门", "排水区域", "人员", "未确定",
)


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


def catalog(report, vision=None):
    return build_evidence_catalog(report_text=report, asr_text=None, vision_text=vision)


# --------------------------------------------------------------------------
# 1. the closed set itself (and why it may not grow)
# --------------------------------------------------------------------------
def test_every_subject_member_has_a_pattern_and_no_pattern_is_orphaned():
    members = set(typing.get_args(Subject)) - {"未确定"}
    assert set(SUBJECT_PATTERNS) == members


def test_the_subject_enum_stays_the_shipped_closed_set():
    """Growing the enum rewrites the pinned response_format and breaks the adapter."""
    assert tuple(typing.get_args(Subject)) == SHIPPED_SUBJECTS


def test_the_prompt_schema_renders_the_subject_as_an_enum():
    rendered = (
        ObservedAnalysisOutput.model_json_schema()["$defs"]["Observation"]["properties"]["subject"]
    )
    assert "enum" in rendered, "subject must reach the prompt as an enum, not free text"
    assert tuple(rendered["enum"]) == SHIPPED_SUBJECTS


def test_the_pinned_response_format_carries_the_same_enum():
    """The adapter compares this object byte-for-byte with its frozen snapshot."""
    contract = work_order_analysis_response_format()
    schema = contract["json_schema"]["schema"]
    rendered = schema["$defs"]["Observation"]["properties"]["subject"]
    assert tuple(rendered["enum"]) == SHIPPED_SUBJECTS


def test_a_subject_outside_the_enum_is_rejected_at_ingress():
    with pytest.raises(Exception):
        ObservedAnalysisOutput.model_validate(dict(
            category="water_supply",
            observations=[dict(label="排水缓慢", subject="卫生间地面砖", evidence_id="report:1")],
            severity="low",
            priority="p3",
            summary="synthetic",
            risk="none",
            required_role="STAFF",
            required_skill="maintenance",
            confidence=0.9,
        ))


# --------------------------------------------------------------------------
# 2. the vocabulary: each fixture is reachable through a shipped subject
# --------------------------------------------------------------------------
@pytest.mark.parametrize("word,sentence", sorted(FIELD_OBJECTS.items()))
def test_object_vocabulary_covers_the_fixture(word, sentence):
    matched = [
        subject for subject, pattern in SUBJECT_PATTERNS.items()
        if re.search(pattern, sentence)
    ]
    assert matched, f"no shipped subject pattern recognises {word}"
    # 地漏-like fixtures must not fall through to nothing; the shipped set has no
    # member for them, so the drainage-area / drainage-joint aliases carry them.
    assert set(matched) & {"排水区域", "排水接头", "阀门管道", "水龙头", "楼道地面"}


@pytest.mark.parametrize("sentence,label,subject", [
    ("卫生间地漏不下水，洗澡的时候积水。", "排水缓慢", "排水区域"),
    ("厨房水槽堵了，水流不下去。", "排水缓慢", "排水区域"),
    ("厨房水槽下方的排水管接头渗水，地上有积水。", "滴渗水", "排水接头"),
    ("卫生间洗手盆下水慢，水放半天才下去。", "排水缓慢", "排水区域"),
    ("阳台排水不通，地面有积水。", "排水缓慢", "排水区域"),
    ("洗衣机出水接口渗水，地上一摊水。", "滴渗水", "排水区域"),
])
def test_the_field_answers_bind_without_any_demotion(sentence, label, subject):
    """Exactly the payloads the model produced in the field now pass as-is."""
    _, trace = bind_observations(output((label, subject, "report:1")), catalog(sentence))
    assert [row["subject"] for row in trace["effective_observations"]] == [subject]
    assert trace["unbound_observations"] == []


# --------------------------------------------------------------------------
# 3. outside the sentence: demoted to unbound, payload survives
# --------------------------------------------------------------------------
REPORT = "厨房水槽堵了，现在水流不下去，需要师傅来看一下。"


def test_object_the_clause_never_names_is_demoted_instead_of_failing():
    _, trace = bind_observations(output(
        ("排水缓慢", "排水区域", "report:1"),
        ("排水缓慢", "水龙头", "report:1"),   # nothing in the sentence says 水龙头
    ), catalog(REPORT))
    assert [row["subject"] for row in trace["effective_observations"]] == ["排水区域"]
    unbound = trace["unbound_observations"]
    assert [(row["label"], row["subject"]) for row in unbound] == [("排水缓慢", "水龙头")]
    assert unbound[0]["reason_code"] == "subject_unsupported"
    assert unbound[0]["disposition"] == "NOT_BOUND_TO_EVIDENCE"
    assert unbound[0]["quote"] == REPORT


def test_the_demoted_row_is_not_rewritten_to_the_accepted_subject():
    _, trace = bind_observations(output(
        ("排水缓慢", "排水区域", "report:1"),
        ("排水缓慢", "水龙头", "report:1"),
    ), catalog(REPORT))
    assert trace["unbound_observations"][0]["subject"] == "水龙头"


def test_nothing_binds_still_fails_the_payload():
    with pytest.raises(Exception) as excinfo:
        bind_observations(
            output(("排水缓慢", "水龙头", "report:1")),
            catalog("厨房水槽堵了，现在水流不下去。"),
        )
    assert "Subject not supported" in str(excinfo.value)


def test_an_object_the_clause_names_differently_stays_fatal():
    """`subject_conflict` is the fabrication guard - it is not demoted."""
    with pytest.raises(Exception) as excinfo:
        bind_observations(
            output(("排水缓慢", "水龙头", "report:1")),
            catalog("卫生间地漏不下水，地面有积水。"),
        )
    assert "Subject not supported" in str(excinfo.value)


def test_a_negated_report_does_not_get_rescued_by_the_demotion():
    with pytest.raises(Exception):
        bind_observations(output(
            ("进水扩散", "排水区域", "report:1"),
            ("排水缓慢", "水龙头", "report:1"),
        ), catalog("卫生间地漏不下水，没有积水。"))


def test_an_unsupported_subject_against_unrelated_evidence_stays_fatal():
    with pytest.raises(Exception):
        bind_observations(output(
            ("进水扩散", "排水区域", "report:1"),
            ("排水缓慢", "水龙头", "vision:1"),
        ), catalog("卫生间地漏不下水，地面有积水。", vision="走廊灯不亮。"))
