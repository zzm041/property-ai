"""Individually valid labels must not contradict business risk or duties."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


def check(text, observations, *, risk, category="electrical", skill="maintenance"):
    raw = dict(category=category, observations=[dict(label=label, subject=subject, evidence_id=eid)
        for label, subject, eid in observations], risk=risk, severity="high", priority="p1",
        summary=text, required_role="STAFF", required_skill=skill, confidence=1.0)
    original = deepcopy(raw)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    result = service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text, asr_text=None, vision_text=None))
    assert raw == original
    return result


@pytest.mark.parametrize("risk", ["none", "other", "flooding", "electric", "gas", "safety"])
def test_confirmed_smoke_cannot_be_described_as_no_or_unrelated_risk(risk):
    mapped, error = check("插座正在冒烟。", [("火花烧损", "插座", "report:1")], risk=risk)
    if risk in {"electric", "gas", "safety"}:
        assert error is None and mapped.risk.value == risk
    else:
        assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("category,skill", [("security", "security"), ("other", "maintenance")])
def test_mixed_security_and_repair_cannot_silently_drop_one_duty(category, skill):
    mapped, error = check("有人在楼下徘徊。房门合不上。", [
        ("人员行为观察", "人员", "report:1"), ("闭合失效", "房门", "report:2")],
        risk="none", category=category, skill=skill)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_multiple_ordinary_repairs_still_use_one_maintenance_duty():
    mapped, error = check("柜门下垂。窗户关不上。", [
        ("部件下垂", "柜门", "report:1"), ("闭合失效", "窗户", "report:2")],
        risk="none", category="other")
    assert error is None and mapped.required_duty == "MAINTENANCE" and mapped.dispatch_fields_applied


def test_equipment_noise_cannot_be_sent_to_security_with_personnel_behavior():
    mapped, error = check("有人在楼下徘徊。空调有噪声。", [
        ("人员行为观察", "人员", "report:1"), ("噪声", "空调", "report:2")],
        risk="none", category="security", skill="security")
    assert mapped is None and error == "AI.INVALID_OUTPUT"


def test_personnel_noise_can_stay_with_personnel_behavior():
    mapped, error = check("有人在楼下徘徊并大声叫喊。", [
        ("人员行为观察", "人员", "report:1"), ("噪声", "人员", "report:1")],
        risk="none", category="security", skill="security")
    assert error is None and mapped.required_duty == "SECURITY" and mapped.dispatch_fields_applied
