"""Evidence-driven contact-only OOD and short gas-smell compatibility."""
import pytest

from tests.unit.test_out_of_scope_request import raw, validate


@pytest.mark.parametrize("source", ["report", "asr"])
@pytest.mark.parametrize("contact", [
    "请只用留言回复我，不要打电话。", "请晚上七点以后联系我。",
    "请先留言，收到我回复后再电话联系，未经确认不要上门。",
    "请先发消息，不要直接上门。", "请明晚八点以后联系我。",
    "请18:30以后电话联系。",
])
def test_contact_only_clause_preserves_manual_scope_route(contact, source):
    mapped, error = validate("请帮我开通门禁。" + contact, source=source)
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.event_trace.get("manual_review_reason") == "OUT_OF_SCOPE_REQUEST"


@pytest.mark.parametrize("extra", [
    "请晚上七点以后联系我，另外设备有个异常。", "请先留言说明水龙头的异常。",
    "我刚才收到电话，说设备不对劲。", "有人直接上门敲门，我不认识他。",
    "请先联系燃气公司维修。", "请先留言，另外有个没说清的问题。",
])
def test_contact_words_cannot_absorb_extra_unknown_facts(extra):
    mapped, error = validate("请帮我开通门禁。" + extra)
    assert error is not None or mapped.event_trace.get("manual_review_reason") is None


def gas_raw(label="疑似燃气气味", risk="gas"):
    return raw((label, "未确定", "report:1"), category="other", required_skill="maintenance",
               risk=risk, severity="high", priority="p1")


@pytest.mark.parametrize("text", [
    "厨房现在有燃气味。", "我闻到了燃气味，具体来源不知道。",
    "厨房现在有燃气味，我不知道从哪里来的。没有看到明火。我已经走到室外，请用电话联系。",
])
def test_short_gas_smell_is_valid_current_gas_evidence(text):
    mapped, error = validate(text, gas_raw())
    assert error is None and mapped.risk == "gas"
    assert any(row["label"] == "疑似燃气气味" for row in mapped.event_trace["effective_observations"])


@pytest.mark.parametrize("text", [
    "厨房没有燃气味。", "昨天厨房有燃气味，现在没有了。",
    "听说厨房有燃气味，我没确认。", "如果厨房有燃气味，请离开。",
    "燃气味检测仪放在厨房。", "燃气没有问题，垃圾有气味。",
])
def test_short_gas_smell_does_not_override_assertion_boundaries(text):
    assert validate(text, gas_raw())[1] == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("risk", ["none", "electric", "flooding", "other"])
def test_short_gas_smell_cannot_be_downgraded_by_risk(risk):
    assert validate("厨房现在有燃气味。", gas_raw(risk=risk))[1] == "AI.INVALID_OUTPUT"


def test_short_gas_smell_cannot_be_omitted_as_pending_observation():
    assert validate("厨房现在有燃气味。", gas_raw(label="现象待核实"))[1] == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("source", ["report", "asr", "vision"])
@pytest.mark.parametrize("text", [
    "如果出现这种情况，厨房有燃气味，请离开。",
    "厨房已经不再有燃气味。", "厨房的燃气味已经消失。",
])
def test_no_current_gas_from_adjacent_hypothesis_or_ended_smell(source, text):
    payload = gas_raw()
    payload["observations"][0]["evidence_id"] = f"{source}:1"
    assert validate(text, payload, source=source)[1] == "AI.INVALID_OUTPUT"


@pytest.mark.parametrize("source", ["report", "asr"])
@pytest.mark.parametrize("text", [
    "厨房现在有燃气味，请不要靠近。",
    "如果方便请联系我，厨房现在有燃气味。",
    "如果方便请先留言，厨房现在有燃气味。",
    "如果方便请发消息，厨房现在有燃气味。",
    "如果方便请给我留言，厨房现在有燃气味。",
    "厨房现在有燃气味，来源不明。",
    "厨房已经不再有燃气味，阳台现在仍有燃气味。",
])
def test_current_gas_not_negated_by_safety_contact_or_another_ended_event(source, text):
    payload = gas_raw()
    payload["observations"][0]["evidence_id"] = f"{source}:1"
    mapped, error = validate(text, payload, source=source)
    assert error is None and mapped.risk == "gas"
