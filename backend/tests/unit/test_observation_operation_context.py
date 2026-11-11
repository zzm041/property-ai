"""Current operation attempts must not erase a local, explicit failure."""
from types import SimpleNamespace

import pytest

from app.domain.ai import build_evidence_catalog
from app.service.analysis import AnalysisService


def validate(text, label, subject, source="report", evidence_index=1):
    raw = dict(category="other", observations=[dict(label=label, subject=subject,
        evidence_id=f"{source}:{evidence_index}")], severity="low", priority="p3", summary=text,
        risk="none", required_role="STAFF", required_skill="maintenance", confidence=1.0)
    service = object.__new__(AnalysisService)
    service._settings = SimpleNamespace(llm_provider="qwen", ai_confidence_threshold=0.0)
    return service._validate_payload(raw, evidence_catalog=build_evidence_catalog(
        report_text=text if source == "report" else None,
        asr_text=text if source == "asr" else None,
        vision_text=text if source == "vision" else None))


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text,label,subject", [
    ("餐边柜左边那扇柜门每次关到最后都会翘开一点，用手扶着也合不上。右边柜门能正常关闭，我没有拆过铰链。", "闭合失效", "柜门"),
    ("储物柜门翘开一点，用手按着也关不上。", "闭合失效", "柜门"),
    ("窗户还留着缝，我用手推着仍然关不齐。", "闭合失效", "窗户"),
    ("卧室门翘开，用手扶住还是合不上。", "闭合失效", "房门"),
    ("次卧顶灯开了以后不亮，插在同屋插座上的台灯还能正常使用。顶灯没有冒烟，也没闻到焦味，具体哪里出了问题还没查。", "照明不亮", "照明"),
    ("灯打开之后还是不亮。", "照明不亮", "照明"),
    ("照明开启后无法点亮。", "照明不亮", "照明"),
    ("灯开了以后不亮，请尽快安排维修。", "照明不亮", "照明"),
    ("柜门翘开一点，用手扶着也合不上，请安排维修。", "闭合失效", "柜门"),
    ("如果方便请安排维修，灯开了以后不亮。", "照明不亮", "照明"),
    ("如果方便请安排维修，柜门翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
])
def test_reported_failure_after_local_action_remains_dispatchable(text, label, subject, source):
    mapped, error = validate(text, label, subject, source)
    assert error is None and mapped.dispatch_fields_applied
    observation = mapped.event_trace["effective_observations"][0]
    assert (observation["label"], observation["subject"]) == (label, subject)


@pytest.mark.parametrize("text,label,subject", [
    ("柜门在这里。用手扶着也合不上。", "闭合失效", "柜门"),
    ("柜门和窗户都翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
    ("柜门翘开一点，窗户用手扶着也合不上。", "闭合失效", "柜门"),
    ("以前柜门翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
    ("听说柜门翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
    ("柜门翘开一点，用手扶着也可能合不上。", "闭合失效", "柜门"),
    ("柜门翘开一点，用手扶着并非合不上。", "闭合失效", "柜门"),
    ("柜门翘开一点，用手扶着也合不上的问题已经修好。", "闭合失效", "柜门"),
    ("灯开了以后没有不亮。", "照明不亮", "照明"),
    ("听说灯开了以后不亮。", "照明不亮", "照明"),
    ("昨天灯开了以后不亮。", "照明不亮", "照明"),
    ("灯开了以后可能不亮。", "照明不亮", "照明"),
    ("灯开了以后不亮但现在已经正常亮起来。", "照明不亮", "照明"),
    ("灯开了以后，屏幕不亮。", "照明不亮", "照明"),
    ("灯在这里。开了以后不亮。", "照明不亮", "照明"),
    ("灯开了以后如果不亮请报修。", "照明不亮", "照明"),
    ("如果灯开了以后不亮，请报修。", "照明不亮", "照明"),
    ("假设灯打开之后还是不亮，请联系维修。", "照明不亮", "照明"),
    ("如果柜门翘开一点，用手扶着也合不上，请报修。", "闭合失效", "柜门"),
    ("假设柜门翘开一点，用手扶着也合不上，请报修。", "闭合失效", "柜门"),
    ("不是灯开了以后不亮，是我还没开灯。", "照明不亮", "照明"),
    ("并非照明开启后无法点亮。", "照明不亮", "照明"),
    ("没有说灯开了以后不亮。", "照明不亮", "照明"),
    ("不是柜门翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
    ("没有说柜门翘开一点，用手扶着也合不上。", "闭合失效", "柜门"),
    ("如果这样，灯开了以后不亮，请报修。", "照明不亮", "照明"),
    ("如果这样，柜门翘开一点，用手扶着也合不上，请报修。", "闭合失效", "柜门"),
])
def test_action_words_do_not_bypass_subject_or_certainty_guards(text, label, subject):
    mapped, error = validate(text, label, subject)
    assert mapped is None and error == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
def test_previous_sentence_condition_does_not_erase_a_new_current_report(source):
    mapped, error = validate("如果别的灯有问题请报修。灯开了以后不亮，请安排维修。",
        "照明不亮", "照明", source, evidence_index=1 if source == "vision" else 2)
    assert error is None and mapped.dispatch_fields_applied
