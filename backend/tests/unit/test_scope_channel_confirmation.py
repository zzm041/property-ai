"""Administrative channel clarification cannot swallow unknown physical faults."""
import pytest
from tests.unit.test_out_of_scope_request import validate


@pytest.mark.parametrize("source", ["report", "asr"])
@pytest.mark.parametrize("text", [
    "请帮我续费停车月卡。请明天上午九点以后联系我，具体怎么办还要向物业确认。",
    "请帮我开通门禁。办理渠道需要向物业核实。",
    "请替我缴停车费。具体办理方式仍需找物业服务中心确认。",
    "请帮我购买食品。请先向物业确认办理流程。",
    "请帮我开通门禁。办理办法向物业咨询。",
])
def test_scope_request_with_complete_channel_confirmation_stays_manual(text, source):
    mapped, error = validate(text, source=source)
    assert error is None and mapped.low_confidence and not mapped.dispatch_fields_applied
    assert mapped.event_trace.get("manual_review_reason") == "OUT_OF_SCOPE_REQUEST"


@pytest.mark.parametrize("extra", [
    "具体情况还要向物业确认。", "故障原因还要向物业确认。",
    "异常是什么东西还要向物业确认。", "办理方式还要向物业确认漏水原因。",
    "具体怎么办还要向物业确认，厨房水龙头在滴水。",
    "具体怎么办还要向物业确认\n餐厅有个说不清的异常。",
    "请先向物业确认办理流程并检查燃气管道。",
    "请向燃气公司确认维修办法。", "先替我向物业执行扣款再确认。",
    "办理方式还要向物业确认，厨房有燃气味。",
])
def test_channel_words_do_not_hide_extra_unknown_or_actionable_facts(extra):
    mapped, error = validate("请帮我开通门禁。" + extra)
    assert error is not None or mapped.event_trace.get("manual_review_reason") != "OUT_OF_SCOPE_REQUEST"
