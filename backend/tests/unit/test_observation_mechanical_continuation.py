"""Omitted mechanical subjects stay local; a nearby object is not free evidence."""
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text, label, subject, source="report"):
    raw = dict(category="other", observations=[dict(label=label, subject=subject,
               evidence_id=f"{source}:1")], severity="low", priority="p3", summary=text,
               risk="none", required_role="STAFF", required_skill="maintenance", confidence=1.0)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    return service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text if source == "report" else None,
        asr_text=text if source == "asr" else None,
        vision_text=text if source == "vision" else None))


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text,label,subject", [
    ("厨房柜门有一边往下垂，关不齐。", "闭合失效", "柜门"),
    ("客厅窗户关上后，锁扣扣不住。", "闭合失效", "窗户"),
    ("储物柜的门推到最后留一道缝，怎么也合不上。", "闭合失效", "柜门"),
    ("卧室柜门向右下方垂。", "部件下垂", "柜门"),
    ("客厅柜门往左下方垂。", "部件下垂", "柜门"),
])
def test_current_mechanical_fact_keeps_its_unique_local_subject(text, label, subject, source):
    mapped, error = validate(text, label, subject, source)
    assert error is None and mapped.dispatch_fields_applied
    assert mapped.event_trace["effective_observations"][0]["subject"] == subject


@pytest.mark.parametrize("text,label,subject", [
    ("柜门和窗户都在这里，关不齐。", "闭合失效", "柜门"),
    ("柜门在这里，窗户关不齐。", "闭合失效", "柜门"),
    ("柜门在这里，锁扣扣不住。", "闭合失效", "窗户"),
    ("以前柜门下垂，关不齐。", "闭合失效", "柜门"),
    ("听说柜门下垂，关不齐。", "闭合失效", "柜门"),
    ("柜门在这里，可能关不齐。", "闭合失效", "柜门"),
    ("柜门在这里，并非关不齐。", "闭合失效", "柜门"),
    ("柜门在这里，外面的东西关不齐。", "闭合失效", "柜门"),
    ("柜门没有向右下方垂。", "部件下垂", "柜门"),
    ("去年柜门向右下方垂。", "部件下垂", "柜门"),
    ("柜门向右移动。", "部件下垂", "柜门"),
])
def test_local_continuation_does_not_launder_other_or_unconfirmed_subject(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_closed_sentence_does_not_supply_missing_subject():
    mapped, error = validate("柜门在这里。关不齐。", "闭合失效", "柜门")
    assert mapped is None and error == "AI.INVALID_OUTPUT"
