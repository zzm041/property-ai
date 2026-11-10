"""Analysis rules: prompts, aliases, dispatch scoring and evidence binding."""

from copy import deepcopy
from hashlib import sha256
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from typing import Mapping

from app.domain.work_order import (
    WorkOrderCategory,
    WorkOrderPriority,
    WorkOrderSeverity,
)

from .errors import ObservationBindingError
from .models import (
    EVENT_CONTRACT,
    Subject,
    Category,
    Severity,
    Priority,
    Risk,
    Observation,
    ObservedAnalysisOutput,
    AiRisk,
    FilterReason,
    SKILL_RESOLUTION_ALIASED,
    SKILL_RESOLUTION_CANONICAL,
    SKILL_RESOLUTION_UNMAPPED,
    WorkOrderAnalysisOutput,
    MappedAnalysis,
    DispatchCandidate,
    DispatchWeights,
)


_INFORMATIONAL_LABELS = frozenset({"外观无明显异常", "现象待核实", "范围外请求"})


_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "work_order_analysis",
        "strict": True,
        "schema": ObservedAnalysisOutput.model_json_schema(),
    },
}


def work_order_analysis_response_format() -> dict[str, object]:
    """Return an isolated canonical contract for provider and adapter guards."""
    return deepcopy(_RESPONSE_FORMAT)


def observation_family(label: str) -> str:
    """Keep existing event identity and danger policy for the explicit alias.

    Raw and effective traces keep the model-selected label. This does not
    merge distinct subjects or remove quote identity for unknown subjects.
    """
    return "火花烧损" if label == "冒烟" else label


def _source_segments(text: str | None) -> list[tuple[str, list[int]]]:
    """Split only on sentence boundaries and retain offsets in the source."""
    if not text:
        return []
    rows = []
    for match in re.finditer(r"[^。！？!?；;\r\n]+(?:[。！？!?；;]|\r\n?|\n|$)", text):
        raw = match.group(0)
        value = raw.strip()
        if value:
            start = match.start() + len(raw) - len(raw.lstrip())
            rows.append((value, [start, start + len(value)]))
    return rows


def _vision_segments(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, list):
        rows = []
        for item in decoded:
            if isinstance(item, dict) and isinstance(item.get("description"), str):
                text = item["description"].strip()
                if text:
                    rows.append(text)
        if rows:
            return rows
    return [value.strip()] if value.strip() else []


def build_evidence_catalog(
    *, report_text: str | None, asr_text: str | None, vision_text: str | None,
) -> dict[str, dict[str, object]]:
    """Create stable per-request ids; history is deliberately not catalogued."""
    catalog: dict[str, dict[str, object]] = {}
    for source, text in (("report", report_text), ("asr", asr_text)):
        for index, (segment, span) in enumerate(_source_segments(text), 1):
            catalog[f"{source}:{index}"] = {"source": source, "text": segment, "span": span}
    for index, segment in enumerate(_vision_segments(vision_text), 1):
        catalog[f"vision:{index}"] = {"source": "vision", "text": segment, "image_index": index}
    return catalog


def prompt_evidence_catalog(catalog: Mapping[str, Mapping[str, object]]) -> list[dict[str, str]]:
    """Expose only immutable id/source/text tuples to the model."""
    return [
        {"id": evidence_id, "source": str(item["source"]), "text": str(item["text"])}
        for evidence_id, item in catalog.items()
    ]


PATTERNS = {
    "滴渗水": r"滴水|滴|渗水|漏水|漏过|喷水|喷出|流出", "不出水": r"不出水|水[^，,。；;!?！？]{0,4}不出|没有水|没水|停水",
    "不能供电": r"没电|没有电|无电|不能供电|断电|停电|插座不能用",
    "照明不亮": r"不亮|无法点亮|不能点亮|还是黑的",
    "操作阻力": r"卡涩|卡住|拧不动|转不动|拉不动|推不动|收不回(?:去)?|(?:拧|转|开关)[^，,。；;!?！？]{0,6}费力",
    "闭合失效": r"关不上|合不上|关不齐|扣不住|无法关闭|无法合上",
    "排水缓慢": r"(?:排水|下水)(?:很|太|非常|特别)?(?:缓慢|慢|不利索)|水[^，,。；;!?！？]{0,12}(?:半天|很久)才下去",
    "部件下垂": r"(?:向|往)(?:左|右)?下方垂|(?:往)?下垂",
    "人员行为观察": r"徘徊|张望|拉(?:动|了)?[^，,。；;!?！？]{0,8}(?:自行车|电动车)",
    "进水扩散": r"进水|水(?:已经|已|正|正在|持续)?扩散",
    "火花烧损": r"火花|冒烟|起火|着火|明火|烧损",
    "冒烟": r"冒烟",
    "不制冷": r"不[制变]?冷|不凉", "噪声": r"声|响", "杂物清扫": r"纸|纸盒|落叶|垃圾|杂物|清扫|包装袋|饭粒|食物残渣",
    "敲门按铃": r"敲门|拍门|按门铃|按铃",
    "疑似燃气气味": r"燃气味|燃气[^，,。；;!?！？\r\n]*(?:气味|异味)|(?:气味|异味)[^，,。；;!?！？\r\n]*燃气",
    "烧焦变色": r"烧(?:焦|黑)|焦黑|熏黑|发黑|黑色.*(?:痕|插孔)",
    "焦煳气味": r"焦(?:煳|糊)?味|烧焦味|糊味",
    "松动": r"松动|松了", "水渍": r"水渍|水迹|积水|棕黄|黄褐|黄色.*斑",
    "表面剥落": r"剥落|脱落|起皮|鼓起|掉.{0,4}(?:块|片)|掉漆|涂层.{0,8}(?:磨损|破损)|露出.{0,8}(?:金属|底色)",
    "外观无明显异常": r"无.*(?:烧|损|烟|异常|渗水|漏水|异味|积水|火花|焦味)|没有.*(?:损|烟|异常|渗水|漏水|异味|积水|火花|焦味)|正常|完好|未见明显",
    "现象待核实": r"不清楚|说不清|无法判断|无法确认|无法描述|不知道|没找到|不确定|未确定|未确认|没确认|没再看到|待核实|可能|怀疑|认为|声称|猜|未见|尚未|矛盾|一人说|另一人说|有人说|说法.{0,4}(?:矛盾|不一)|没有|没说|没查|不是|漏听|没.字",
    "范围外请求": r"(?:开通|办理|申请|授权)(?:(?:我|我们|他|她|朋友|访客|的|新|小区|单元))*门禁|"
                r"(?:缴纳|缴|交|支付|代付)(?:(?:我|我们|他|她|朋友|的|本月|下月))*(?:停车费|物业费)|"
                r"(?:停车费|物业费)(?:应该|可以|要)?(?:怎么|如何)(?:缴|交|付)|"
                r"(?:办理|续办|购买|续费)(?:(?:我|我们|他|她|朋友|的|本月|下月))*停车(?:包月|月卡)|"
                r"(?:订购|代购|购买)(?:[一二两三四五六七八九十百0-9]+(?:袋|箱|份|件|个))?(?:大米|商品|食品|日用品)",
}


SUBJECT_PATTERNS = {
    "窗户": r"窗户|纱窗|窗框", "柜门": r"柜门|柜的门", "排水区域": r"排水|下水|地漏|淋浴|洗澡",
    "人员": r"人|他|她",
    "水龙头": r"水龙头|龙头", "排水接头": r"排水|接头|洗手盆|水槽", "天花墙面": r"天花|墙|涂层",
    "插座": r"插座|插孔|插排|排插", "照明": r"灯|照明", "空调": r"空调", "扶手": r"扶手",
    "楼道地面": r"楼道|走廊|楼梯口|电梯厅|角落|地面|地板|地上|通道", "房门": r"门", "阀门管道": r"阀|管|软管",
}


_UNCERTAIN = re.compile(r"猜|怀疑|认为|声称|可能|也许|好像|听说|据说|一人说|另一人说|有人说|他说|她说|转述|邻居说|家人说|对方说|无法判断|无法确认|不知道|未确认|没.*确认|尚未")


_HISTORY = re.compile(r"以前|上次|历史|去年|早先|曾经|昨天|前天|昨晚|上月|上个月")


_CURRENT_TIME = re.compile(r"今天|现在|目前|此刻|刚才|正在|又开始|仍在|还在|当下|本次")


_NEGATIVE = re.compile(r"(?:并非|不是|未见|没有|没|无|未)(?:正在|发生|明显|任何|出现|看到|发现|再)?[^，,。；;!?！？]{0,4}$")


_POST_NEGATIVE = re.compile(r"^[”’\"\s]*(?:并|从|始终|根本)?(?:没有|未|不曾|不存在)")


_ACTION_LIMITATION_PREFIX = re.compile(r"(?:无法|没法|不能|难以|很难|不容易)$")


_POST_RESOLVED = re.compile(r"^(?:的(?:问题|故障))?(?:已经|已|现已)(?:修好|修复|解决|恢复正常)")


_OBSERVABLE_SUBJECT_PREFIX = {
    "照明不亮": re.compile(r"(?:灯|照明)(?P<operation>(?:打开|开启|开)(?:了)?(?:开关)?(?:以后|之后|后))?"
                         r"(?:(?:现在|目前|一直|已经|突然|还是|也|仍然|都|完全|就是|怎么也|始终|又))*$"),
    "进水扩散": re.compile(r"(?:地面|地板|地上)(?:(?:现在|目前|正在|持续|不断|一直|已经|开始|又|正|已|还在|仍在|上|的))*$"),
}


_LEADING_UNCERTAIN_SPEAKER = re.compile(
    r"^(?:我|本人|用户|住户|邻居|家人|对方|他|她|有人|一人|另一人|维修人员|工作人员)"
)


_SUBJECTLESS_CONTINUATION_PREFIX = {
    "不制冷": re.compile(r"^(?:出风|送风)(?:但|却|仍|仍然|还是)?$")
}


_GENERIC_SUBJECT_CONTINUATION = re.compile(
    r"^(?:现在(?:每隔(?:几|[一二三四五六七八九十0-9]+)(?:秒|分钟|分|小时))?|"
    r"目前|此刻|刚才|随后|之后|仍|仍然|还|还在|正|正在|已经|已|尚未|不再|持续|"
    r"还在(?:缓慢|持续)|"
    r"水(?:正|一直|持续|不断)?|表面|外观|一人说|另一人说|有人说)$"
)


_OPERATION_CONDITIONAL = re.compile(r"如果|假如|假设|一旦|万一|若|是否|会不会|有没有")


_OPERATION_NONASSERTIVE = re.compile(_OPERATION_CONDITIONAL.pattern + r"|不是|并非")


_COMPLETE_COURTESY_REQUEST = re.compile(
    r"(?:如果|假如|若)(?:方便|有空|有时间)(?:的话)?(?:请|麻烦)(?:尽快)?"
    r"(?:安排)?(?:维修|处理|检查|查看)(?:一下)?"
)


def _is_leading_uncertain_modifier(text: str) -> bool:
    """Return whether a short standalone clause governs the following clause."""
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 16:
        return False
    core = _LEADING_UNCERTAIN_SPEAKER.sub("", compact, count=1)
    return bool(
        _UNCERTAIN.fullmatch(core)
        or re.fullmatch(r"(?:说|表示|猜测|据[^，,。；;!?！？]{1,12}说|传闻)", core)
    )


def _supported_subjects(text: str, *, continuation_subject: str | None = None) -> set[str]:
    subjects = {subject for subject, pattern in SUBJECT_PATTERNS.items() if re.search(pattern, text)}
    # A speaker is not another faulty object; an explicitly named connection
    # is more specific than the drainage area that its name also matches.
    if not re.search(PATTERNS["人员行为观察"], text):
        subjects.discard("人员")
    if "排水接头" in subjects and "接头" in text:
        subjects.discard("排水区域")
    elif {"排水接头", "排水区域"} <= subjects:
        # Preserve the old leak alias during omitted-subject resolution only.
        # New drainage-slow observations separately require an explicit joint.
        if continuation_subject == "排水接头":
            subjects.discard("排水区域")
        elif continuation_subject == "排水区域":
            subjects.discard("排水接头")
    if "柜门" in subjects:
        subjects.discard("房门")
    return subjects


def _previous_clause_start(text: str, clause_start: int) -> int:
    end = clause_start - 1
    return max((text.rfind(char, 0, end) for char in "，,。；;!?！？"), default=-1) + 1


def _previous_clause(text: str, clause_start: int) -> str:
    if clause_start <= 0:
        return ""
    end = clause_start - 1
    return text[_previous_clause_start(text, clause_start):end].strip()


def _adjacent_operation_condition(text: str, clause_start: int) -> bool:
    # A conditional lead can govern one adjacent comma-delimited topic; do
    # not propagate it across sentence boundaries or arbitrarily far back.
    if clause_start <= 0 or text[clause_start - 1] not in "，,":
        return False
    prior = _previous_clause(text, clause_start)
    # A complete courtesy request has its own conditional consequence;
    # 'if convenient, arrange repair' does not qualify the next fault.
    return bool(_OPERATION_CONDITIONAL.match(prior)
                and _COMPLETE_COURTESY_REQUEST.fullmatch(prior) is None)


def _prior_clause_carries_unique_subject(
    text: str, clause_start: int, current_prefix: str, subject: str, *, label: str,
) -> bool:
    """Resolve a nearby omitted Chinese subject without crossing sentences.

    The continuation must follow a comma/semicolon, contain no competing
    catalogued subject, and the immediately preceding clause must contain the
    one requested subject without hearsay or historical qualification.
    """
    if clause_start <= 0 or text[clause_start - 1] not in "，,；;":
        return False
    compact = re.sub(r"\s+", "", current_prefix)
    # Only closing predicates admit an empty prefix or a local latch. Do not
    # extend empty-subject borrowing to arbitrary noise, water or danger facts.
    mechanical_closure = (
        label == "闭合失效" and subject in {"房门", "窗户", "柜门"}
        and re.fullmatch(r"(?:怎么也|锁扣|(?:我)?用手(?:扶|按|推|托)(?:着|住)?(?:也|还是|仍然|仍))?", compact) is not None
    )
    if not mechanical_closure and (
            not compact or _GENERIC_SUBJECT_CONTINUATION.fullmatch(compact) is None):
        return False
    prior = _previous_clause(text, clause_start)
    if mechanical_closure and "用手" in compact:
        topic = re.search(SUBJECT_PATTERNS[subject], prior)
        if (_OPERATION_NONASSERTIVE.search(prior)
                or (topic and _NEGATIVE.search(prior[:topic.start()]))
                or _adjacent_operation_condition(text, _previous_clause_start(text, clause_start))):
            return False
    return (
        _supported_subjects(prior, continuation_subject=subject) == {subject}
        and not _UNCERTAIN.search(prior)
        and not _HISTORY.search(prior)
        and not _NEGATIVE.search(prior)
        and not _POST_NEGATIVE.search(prior)
    )


def _lighting_switch_continuation(text: str, clause_start: int, prefix: str) -> bool:
    """Resolve a bounded lamp topic and an explicit local switching action."""
    if prefix.strip():
        # A current lamp topic may be followed by 'opened, then not lit'.
        # Keep this separate from arbitrary empty-prefix subject borrowing.
        if (clause_start <= 0 or text[clause_start - 1] not in "，,"
                or re.fullmatch(
                    r"(?:打开|开启|开)(?:了)?(?:以后|之后|后)(?:还是|仍然|仍|也)?",
                    prefix.strip()) is None):
            return False
        topic = _previous_clause(text, clause_start)
        topic_start = _previous_clause_start(text, clause_start)
        lead = (_previous_clause(text, topic_start)
                if topic_start > 0 and text[topic_start - 1] in "，," else "")
        if (_is_leading_uncertain_modifier(lead)
                or (_HISTORY.search(lead) and not _CURRENT_TIME.search(topic))):
            return False
        courtesy = re.fullmatch(
            r"(?:如果|假如|若)(?:方便|有空|有时间)(?:的话)?(?:请|麻烦)(?P<request>.+)", lead)
        contact_condition = courtesy is not None and _scope_contact_clause(courtesy["request"])
        return bool(
            re.fullmatch(r"[^，,。；;!?！？和与及、\s]{0,16}(?:灯|照明)", topic)
            and len(re.findall(SUBJECT_PATTERNS["照明"], topic)) == 1
            and _supported_subjects(topic) <= {"照明", "楼道地面"}
            and not _HISTORY.search(topic) and not _UNCERTAIN.search(topic)
            and not _OPERATION_NONASSERTIVE.search(topic)
            and not re.search(r"没有|未见|没|未|不曾|不确定|不要|禁止|勿|别", topic)
            and (not _adjacent_operation_condition(text, topic_start) or contact_condition)
        )
    previous = text[:clause_start]
    parts = re.fullmatch(r"([^，,。；;!?！？]+)[，,](按(?:了)?(?:[一二三四五六七八九十两0-9]+[次回下])?开关)[，,]", previous)
    if parts is None:
        return False
    topic = parts[1].strip()
    return bool(
        re.fullmatch(r"[^，,。；;!?！？和与及、]{0,12}(?:灯|照明)", topic)
        and not _HISTORY.search(topic) and not _UNCERTAIN.search(topic)
        and not re.search(r"没有|不是|并非|未见", topic)
    )


def _clause_for_match(text: str, match: re.Match[str]) -> tuple[str, int]:
    delimiters = "，,。；;!?！？"
    left = max((text.rfind(char, 0, match.start()) for char in delimiters), default=-1)
    rights = [position for char in delimiters if (position := text.find(char, match.end())) >= 0]
    right = min(rights) if rights else len(text)
    clause_start = left + 1
    clause = text[clause_start:right]
    if left >= 0:
        prior_right = left
        prior_left = max((text.rfind(char, 0, prior_right) for char in delimiters), default=-1)
        prior = text[prior_left + 1:prior_right].strip()
        if _is_leading_uncertain_modifier(prior) or (
            _HISTORY.search(prior) and not _CURRENT_TIME.search(clause)
        ):
            clause = prior + "，" + clause
            clause_start = prior_left + 1
    return clause, clause_start


_UNSPECIFIED_PROBLEM = re.compile(r"(?:有|出了?|出现了?)(?:点|些|个)?问题|出(?:了)?故障|(?:有点)?(?:不对劲|不太对)")


_UNSPECIFIED_CLAUSE = re.compile(
    r"(?:(?:我家|这里|这边|现在|目前|今天)?(?:" + _UNSPECIFIED_PROBLEM.pattern + r"))|"
    r"(?:(?:请|麻烦)?(?:帮忙|帮我)?(?:处理(?:一下)?|看(?:看|一下)))|(?:请)?帮忙|谢谢|麻烦了"
)


def _unspecified_current_matches(
    output: ObservedAnalysisOutput, item: Observation, quote: str,
    catalog: Mapping[str, Mapping[str, object]],
) -> list[re.Match[str]]:
    """Accept a vague report only as non-dispatching clarification evidence.

    Inspect all current evidence, not just the model's selected observations:
    an omitted concrete fault must not become a generic clarification success.
    """
    if (output.category != "other" or output.required_skill != "other"
            or output.risk != "none" or item.subject != "未确定"
            or any(o.label != "现象待核实" for o in output.observations)
            or any(e.get("source") not in {"report", "asr"} for e in catalog.values())):
        return []
    if _HISTORY.search(quote) or re.search(r"昨天|前天|之前|已修复|已解决|已处理", quote):
        return []
    # A finite danger vocabulary cannot prove absence of danger. This extra
    # acceptance path admits only wholly vague/polite clauses; any additional
    # content (even an unknown danger phrase) remains on the rejection path.
    clauses = [part.strip() for e in catalog.values()
               for part in re.split(r"[，,。；;!?！？\n]+", str(e.get("text") or "")) if part.strip()]
    if not clauses or any(_UNSPECIFIED_CLAUSE.fullmatch(part) is None for part in clauses):
        return []
    if any(re.search(pattern, str(e.get("text") or ""))
           for label, pattern in PATTERNS.items() if label not in _INFORMATIONAL_LABELS
           for e in catalog.values()):
        return []
    return [m for m in _UNSPECIFIED_PROBLEM.finditer(quote)
            if not _NEGATIVE.search(quote[:m.start()])]


def _confirmed_label_matches(label: str, quote: str, matches: list[re.Match[str]]):
    """Shared certainty rules for binding and bounded danger-category checks."""
    confirmed = []
    postposed_negative = False
    historical = False
    uncertain = False
    prefixed_negative = False
    action_limitation = False
    for candidate in matches:
        clause, clause_start = _clause_for_match(quote, candidate)
        if label == "疑似燃气气味" and not _danger_match_is_assertion(quote, candidate):
            uncertain = True
            continue
        certainty_clause = (
            re.sub(r"不知道是谁|身份不明|不认识[他她]", "", clause)
            if label == "人员行为观察" else clause
        )
        if _UNCERTAIN.search(certainty_clause):
            uncertain = True
            continue
        if _HISTORY.search(clause):
            historical = True
            continue
        local_start = candidate.start() - clause_start
        local_end = candidate.end() - clause_start
        prefix = clause[:local_start]
        suffix = clause[local_end:]
        if label == "疑似燃气气味":
            courtesy = re.fullmatch(
                r"(?:如果|假如|若)(?:方便|有空|有时间)(?:的话)?(?:请|麻烦)"
                r"(?P<request>.+)", _previous_clause(quote, clause_start))
            contact_condition = courtesy is not None and _scope_contact_clause(courtesy["request"])
            if _adjacent_operation_condition(quote, clause_start) and not contact_condition:
                uncertain = True
                continue
            if (re.search(r"(?:不再|不曾)(?:有|闻到|闻见)?$|闻不到$", prefix)
                    or re.match(r"^(?:已经|已|现已)?(?:消失|散去|散尽|没有了)", suffix)):
                historical = True
                continue
        if label in {"冒烟", "火花烧损"} and candidate.group() == "冒烟":
            # Both labels and the omission scan share smoke certainty rules.
            # Match-specific filtering still allows other current sparks in
            # the same quote; historical records are never rewritten.
            if (not _danger_match_is_assertion(quote, candidate)
                    or _adjacent_operation_condition(quote, clause_start)):
                continue
            if re.search(r"(?:停止|不再|不曾)(?:了|继续|持续)?$", prefix):
                historical = True
                continue
            if re.search(r"^(?:但|不过)?(?:现在|目前)?(?:不是|并非)", prefix):
                prefixed_negative = True
                continue
        if (label == "人员行为观察" and re.search(
                r"(?:没有|不是|并非|未见|没看到|未发现)(?:有)?(?:个)?人[^，,。；;!?！？]*$", prefix)):
            prefixed_negative = True
            continue
        relation = _OBSERVABLE_SUBJECT_PREFIX.get(label)
        relation_match = relation.search(prefix) if relation is not None else None
        if (label == "照明不亮" and relation_match is not None
                and relation_match.group("operation") is not None):
            # The added operation phrase separates a negation from the fault
            # predicate. Check the lamp topic's own lead as well as the old
            # short predicate prefix; only look before the reported failure.
            lead = prefix[:relation_match.start()]
            if (_OPERATION_NONASSERTIVE.search(lead) or _NEGATIVE.search(lead)
                    or _adjacent_operation_condition(quote, clause_start)):
                continue
        if (relation is not None and relation.search(prefix) is None
                and not (label == "照明不亮" and _lighting_switch_continuation(quote, clause_start, prefix))):
            continue
        if (label == "照明不亮" and re.search(
                r"^(?:但|不过)?(?:现在|目前)?(?:已经|已)(?:正常亮起来|亮起来|恢复照明)"
                r"|^(?:但|不过|现在|目前)正常亮", suffix)):
            historical = True
            continue
        if _POST_RESOLVED.search(suffix):
            historical = True
            continue
        if (label == "不能供电"
                and candidate.group() in {"断电", "停电"}
                and _ACTION_LIMITATION_PREFIX.search(prefix)):
            action_limitation = True
            continue
        if label != "外观无明显异常" and _NEGATIVE.search(prefix):
            prefixed_negative = True
            continue
        if label != "外观无明显异常" and _POST_NEGATIVE.search(suffix):
            postposed_negative = True
            continue
        confirmed.append((candidate, clause, clause_start))
    if not confirmed and postposed_negative:
        rejection_kind = "postposed_negative"
        raise ObservationBindingError("Postposed negated observation", reason_code=rejection_kind)
    if not confirmed and action_limitation:
        rejection_kind = "action_limitation_reported"
        raise ObservationBindingError(
            "Reported limitation on cutting power, not an outage observation",
            reason_code=rejection_kind)
    if not confirmed and prefixed_negative:
        rejection_kind = "prefixed_negative"
        raise ObservationBindingError("Negated observation", reason_code=rejection_kind)
    if not confirmed and uncertain:
        rejection_kind = "uncertain"
        raise ObservationBindingError("Observation is not confirmed current evidence", reason_code=rejection_kind)
    if not confirmed and historical:
        rejection_kind = "historical"
        raise ObservationBindingError("Observation is not confirmed current evidence", reason_code=rejection_kind)
    if not confirmed:
        rejection_kind = "unconfirmed_current"
        raise ObservationBindingError("Observation is not confirmed current evidence", reason_code=rejection_kind)
    return confirmed


_DANGER_NONASSERTIVE = re.compile(
    r"如果|假如|一旦|万一|假设|若|防止|避免|禁止|严禁|不得|不要|请勿|"
    r"是否|有没有|会不会|担心|害怕|说明书|规定|演练|模拟"
)


def _danger_match_is_assertion(quote: str, match: re.Match[str]) -> bool:
    clause, start = _clause_for_match(quote, match)
    prefix = clause[:match.start() - start]
    suffix = clause[match.end() - start:]
    # A warning *after* an observed danger does not negate that observation:
    # 'currently smoking, do not approach' is not 'do not cause smoke'.
    if _DANGER_NONASSERTIVE.search(prefix) or suffix.startswith("的话"):
        return False
    if match.group() == "明火" and suffix.startswith("作业"):
        return False
    if match.group().endswith(("气味", "异味")) and re.match(r"检测仪|传感器|检测器", suffix):
        return False
    return True


def _confirmed_danger_categories(catalog: Mapping[str, Mapping[str, object]]) -> set[str]:
    """Find bounded current danger categories, not every subject or location.

    Search each clause separately: the legacy gas pattern must not connect
    'gas is fine' with rubbish smell in the next clause. Match offsets still
    refer to the whole quote so leading hearsay/history keeps its scope.
    """
    labels = set()
    for evidence in catalog.values():
        quote = str(evidence.get("text") or "")
        for label in {"火花烧损", "疑似燃气气味"} - labels:
            pattern = re.compile(PATTERNS[label])
            matches = [match for clause in re.finditer(r"[^，,。；;!?！？\n]+", quote)
                       for match in pattern.finditer(quote, clause.start(), clause.end())
                       if _danger_match_is_assertion(quote, match)]
            try:
                _confirmed_label_matches(label, quote, matches)
            except ObservationBindingError:
                continue
            labels.add(label)
    return labels


def _scope_request_matches(quote: str) -> list[re.Match[str]]:
    """Bounded unsupported business requests, not device-noun classification."""
    result = []
    for match in re.finditer(PATTERNS["范围外请求"], quote):
        clause, start = _clause_for_match(quote, match)
        prefix = clause[:match.start() - start]
        if (_HISTORY.search(clause)
                or re.search(r"听说|据说|转述|有人说|他说|她说|假如|如果|假设|一旦|万一", clause)
                or re.search(r"(?:不|没|无需|不用|不要)(?:想|要|需要|希望)?$", prefix)):
            continue
        # Asking how to perform an unsupported service is itself a request;
        # uncertainty about its procedure is not uncertainty about the intent.
        if not re.search(r"请|帮|麻烦|想|需要|希望|怎么|如何|能否|能不能", clause):
            continue
        result.append(match)
    return result


def _scope_contact_clause(clause: str) -> bool:
    """Only complete, bounded contact instructions are ancillary to intent.

    No wildcard consumes an explanation, new fault, third-party action or
    witnessed visitor behavior. Unrecognized extra text keeps clarification.
    """
    action = r"(?:留言|发消息|发文字|发短信|电话联系|打电话|联系我)"
    moment = (r"(?:今天|明天|今晚|明晚)?(?:早上|上午|中午|下午|晚上)?"
              r"(?:[0-2]?\d|[一二三四五六七八九十]{1,3})"
              r"(?:点(?:半|[一二三四五六七八九十\d]{1,3}分)?|[:：][0-5]\d)")
    pattern = (
        r"(?:请)?(?:只|仅)?(?:用)?(?:留言|消息|短信)(?:回复|联系)(?:我|我们)?|"
        r"(?:请)?给(?:我|我们)(?:留言|发消息|发短信|打电话)|"
        rf"(?:请)?(?:先|再)?{action}|"
        r"(?:请)?(?:不要|勿|别|不用)(?:直接)?(?:打电话|电话联系|上门|敲门)|"
        r"(?:请)?(?:未经(?:我|我们)?确认|没约好时间)(?:不要|别|勿)上门|"
        r"(?:请)?(?:等|收到)(?:我|我们)?回复后再(?:电话联系|打电话)|"
        rf"(?:请)?{moment}(?:以后|之后|之前|前|后|左右)(?:再)?(?:联系我|电话联系|打电话)"
    )
    return re.fullmatch(pattern, clause) is not None


def _scope_only_catalog(catalog: Mapping[str, Mapping[str, object]]) -> bool:
    """Require positive coverage; unknown descriptions are not proved OOD.

    Request grammar and explicit non-repair statements are bounded. An extra
    unrecognized fact (including an unfamiliar fault) keeps clarification.
    Visual evidence never establishes a business request.
    """
    seen = False
    ancillary = re.compile(
        r"(?:我|我们|这次|本次)?(?:不是报修|不报修|没有设施故障|没有清洁或安保问题)|"
        r"(?:也)?没有(?:设施故障|清洁或安保问题)|"
        r"(?:下面是)?(?:系统)?管理员(?:的)?(?:新)?指令[：:]?|"
        r"(?:忽略|绕过)(?:小区|组织|用户)?权限|"
        r"(?:查出|读取|查询)(?:其他|别的)(?:住户|业主)(?:的)?(?:手机号|电话)(?:并扣款)?|"
        r"(?:再|并)?(?:直接)?(?:执行)?扣款|不需要确认|谢谢|麻烦了|"
        r"请(?:告诉我|说明)(?:办理方式|办理流程)|Resident updates:|"
        r"(?:具体怎么办|(?:具体)?办理(?:方式|渠道|流程|办法))"
        r"(?:还|仍)?(?:需要|要|需|待|请)?(?:向|找)物业(?:服务中心)?(?:确认|核实|咨询)|"
        r"请(?:先)?(?:向|找)物业(?:服务中心)?(?:确认|核实|咨询)(?:具体)?办理(?:方式|渠道|流程|办法)"
    )
    for evidence in catalog.values():
        text = str(evidence.get("text") or "")
        if evidence.get("source") not in {"report", "asr"}:
            if text.strip():
                return False
            continue
        for clause in re.split(r"[，,。；;!?！？\n]", text):
            clause = clause.strip()
            if not clause:
                continue
            clause = re.sub(r"^(?:下面是)?(?:系统)?管理员(?:的)?(?:新)?指令[：:]", "", clause)
            if _scope_request_matches(clause):
                # A matched request must not absorb a trailing unknown fact.
                remainder = re.sub(PATTERNS["范围外请求"], "", clause)
                remainder = re.sub(
                    r"(?:不知道|怎么|如何|请问|请|帮|替|麻烦|想|需要|希望|让|给|我|我们|"
                    r"朋友|物业|他|她|并|还|也|一袋|直接|从账户扣款|告诉我办理方式)", "", remainder)
                if remainder.strip():
                    return False
                seen = True
            elif not ancillary.fullmatch(clause) and not _scope_contact_clause(clause):
                return False
    return seen


def _confirmed_actionable_categories(catalog: Mapping[str, Mapping[str, object]]) -> set[str]:
    """Prevent a scope-only classification from concealing known current faults.

    This uses the existing finite observation vocabulary, not a universal
    semantic completeness check, and never assigns a subject or business role.
    """
    found = set()
    # Broad labels have permissive binding vocabulary when the model already
    # chose a phenomenon. Omission detection needs explicit fault wording;
    # paper contact instructions and a request to say something are not faults.
    omission_patterns = {
        "滴渗水": r"滴水|渗水|漏水|喷水|喷出",
        "杂物清扫": r"垃圾|杂物|食物残渣|(?:散落|散着|堆着|堆积)[^，,。；;!?！？]{0,8}(?:纸|包装袋|落叶|饭粒)",
        "噪声": r"噪声|噪音|异响|吵闹|嗡嗡声",
    }
    for evidence in catalog.values():
        quote = str(evidence.get("text") or "")
        for label in PATTERNS.keys() - _INFORMATIONAL_LABELS:
            matches = [match for clause in re.finditer(r"[^，,。；;!?！？\n]+", quote)
                       for match in re.compile(omission_patterns.get(label, PATTERNS[label])).finditer(quote, clause.start(), clause.end())
                       if _danger_match_is_assertion(quote, match)]
            try:
                _confirmed_label_matches(label, quote, matches)
            except ObservationBindingError:
                continue
            found.add(observation_family(label))
    return found


def bind_observations(
    output: ObservedAnalysisOutput, catalog: Mapping[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]]:
    normalized = []
    rejected = []
    for item in output.observations:
        evidence = catalog.get(item.evidence_id)
        if evidence is None:
            raise ObservationBindingError("Observation evidence id is not in the current catalog",
                                          reason_code="unknown_evidence")
        quote = str(evidence.get("text") or "")
        source = str(evidence.get("source") or "")
        if not quote or item.evidence_id.split(":", 1)[0] != source:
            raise ObservationBindingError("Observation evidence catalog is invalid", reason_code="invalid_catalog")
        rejection_kind = None
        unspecified_current = False
        try:
            if item.label == "范围外请求":
                if source not in {"report", "asr"} or item.subject != "未确定":
                    rejection_kind = "scope_request_source_invalid"
                    raise ValueError("Scope request requires current resident text or transcript")
                matches = _scope_request_matches(quote)
            else:
                matches = list(re.finditer(PATTERNS[item.label], quote))
            if not matches and item.label == "现象待核实":
                matches = _unspecified_current_matches(output, item, quote, catalog)
                unspecified_current = bool(matches)
            if not matches:
                rejection_kind = "label_not_matched"
                raise ValueError("Evidence does not support observable label")
            selected_clause = quote
            selected_clause_start = 0
            if item.label not in {"现象待核实", "范围外请求"}:
                try:
                    confirmed = _confirmed_label_matches(item.label, quote, matches)
                except ObservationBindingError as exc:
                    rejection_kind = exc.reason_code
                    raise
                # One source sentence can describe the same fault on multiple
                # objects. Prefer evidence for the claimed object, rather than
                # reducing it based only on the first occurrence of the label.
                supported = [row for row in confirmed if item.subject != "未确定"
                             and re.search(SUBJECT_PATTERNS[item.subject], row[1])]
                match, selected_clause, selected_clause_start = (supported or confirmed)[0]
            else:
                match = matches[0]
                selected_clause, selected_clause_start = _clause_for_match(quote, match)
            if (item.label == "排水缓慢" and item.subject == "排水接头"
                    and "接头" not in selected_clause):
                rejection_kind = "subject_conflict"
                raise ValueError("Subject not supported by the selected evidence")
            if (item.label in {"闭合失效", "部件下垂", "操作阻力"} and item.subject == "房门"
                    and re.search(SUBJECT_PATTERNS["柜门"], selected_clause)
                    and not re.search(r"房门|入户门|卧室门", selected_clause)):
                rejection_kind = "subject_conflict"
                raise ValueError("Subject not supported by the selected evidence")
            if item.subject != "未确定" and not re.search(SUBJECT_PATTERNS[item.subject], selected_clause):
                clause_subjects = _supported_subjects(selected_clause)
                local_start = match.start() - selected_clause_start
                prefix = selected_clause[:local_start].strip()
                continuation = _SUBJECTLESS_CONTINUATION_PREFIX.get(item.label)
                prior_clause = _previous_clause(quote, selected_clause_start)
                explicit_continuation = (
                    continuation is not None
                    and continuation.fullmatch(prefix) is not None
                    and selected_clause_start > 0
                    and quote[selected_clause_start - 1] in "，,；;"
                    and _supported_subjects(prior_clause, continuation_subject=item.subject) == {item.subject}
                    and not _UNCERTAIN.search(prior_clause)
                    and not _HISTORY.search(prior_clause)
                    and not _NEGATIVE.search(prior_clause)
                    and not _POST_NEGATIVE.search(prior_clause)
                )
                unique_subject_continuation = _prior_clause_carries_unique_subject(
                    quote, selected_clause_start,
                    prefix if item.label == "闭合失效" else (prefix or match.group(0)),
                    item.subject, label=item.label,
                )
                if item.label == "照明不亮" and item.subject == "照明":
                    unique_subject_continuation |= _lighting_switch_continuation(quote, selected_clause_start, prefix)
                if clause_subjects or not (explicit_continuation or unique_subject_continuation):
                    # A location ("楼道里有焦味") does not establish the
                    # object's identity. Only a directly attached subject and
                    # predicate establish this bounded conflict guard.
                    # Common-area names locate a smell even without "里";
                    # they do not identify its emitting surface or equipment.
                    direct_subject = any(
                        re.search(r"(?:" + (
                                      r"地面|地板|地上" if subject == "楼道地面"
                                      and item.label in {"焦煳气味", "疑似燃气气味"}
                                      else SUBJECT_PATTERNS[subject]
                                  ) + r")(?:面)?"
                                  r"(?:(?:现在|目前|正在|已经|持续|一直|明显|出现|发生|开始|还在|仍在|有|已|也|又))*$",
                                  prefix)
                        for subject in clause_subjects
                    )
                    rejection_kind = (
                        "subject_conflict" if direct_subject and item.label not in _INFORMATIONAL_LABELS
                        else "subject_unsupported"
                    )
                    raise ValueError("Subject not supported by the selected evidence")
        except ValueError as exc:
            rejected.append({
                **item.model_dump(mode="json"),
                "source": source,
                "quote": quote,
                "reason": str(exc),
                **({"reason_code": rejection_kind} if rejection_kind else {}),
                **({"span": evidence["span"]} if "span" in evidence else {}),
            })
            continue
        normalized.append({
            "label": item.label,
            "subject": item.subject,
            "evidence_id": item.evidence_id,
            "source": source,
            "quote": quote,
            "effective": item.label not in _INFORMATIONAL_LABELS,
            **({"acceptance_reason": "UNSPECIFIED_CURRENT_REPORT"} if unspecified_current else {}),
            **({"span": evidence["span"]} if "span" in evidence else {}),
        })
    # A model may correctly extract several current phenomena from one server
    # evidence segment yet over-attach the one explicit component to every
    # phenomenon.  Keep the raw output, and only reduce that unsupported
    # subject certainty when the same evidence independently contains an
    # accepted current observation for the claimed component.  This preserves
    # the phenomenon and risk without treating an unknown subject as a
    # cross-object wildcard.  Standalone or competing-subject mismatches still
    # fail closed below.
    adjusted = []
    still_rejected = []
    for row in rejected:
        informational_clarification = (
            row["label"] in _INFORMATIONAL_LABELS
            and (
                (output.category == "other" and output.required_skill == "other")
                or (
                    row["label"] == "现象待核实"
                    and row["source"] in {"report", "asr"}
                        and row["subject"] in SUBJECT_PATTERNS
                    and bool(re.search(SUBJECT_PATTERNS[row["subject"]], row["quote"]))
                    and all(item.label in _INFORMATIONAL_LABELS for item in output.observations)
                )
            )
        )
        can_reduce_subject = (
            row.get("reason_code") == "subject_unsupported"
            and (
                informational_clarification
                or (
                    row["label"] not in _INFORMATIONAL_LABELS
                    and any(
                        accepted["effective"]
                        and accepted["evidence_id"] == row["evidence_id"]
                        and accepted["subject"] == row["subject"]
                        for accepted in normalized
                    )
                )
            )
        )
        if not can_reduce_subject:
            still_rejected.append(row)
            continue
        accepted = {
            "label": row["label"],
            "subject": "未确定",
            "evidence_id": row["evidence_id"],
            "source": row["source"],
            "quote": row["quote"],
            "effective": row["label"] not in _INFORMATIONAL_LABELS,
            **({"span": row["span"]} if "span" in row else {}),
        }
        normalized.append(accepted)
        adjusted.append({
            **accepted,
            "raw_subject": row["subject"],
            "disposition": "SUBJECT_CERTAINTY_REDUCED",
            "adjustment_reason": (
                "the phenomenon is supported, but its clause does not support the model-selected subject"
            ),
        })
    rejected = still_rejected
    effective = [row for row in normalized if row["effective"]]
    scope_requests = [row for row in normalized if row["label"] == "范围外请求"]
    if scope_requests:
        if not effective and (output.category != "other" or output.required_skill != "other" or output.risk != "none"):
            raise ObservationBindingError("Scope-only request conflicts with dispatch fields",
                                          reason_code="scope_request_fields_conflict")
        if _confirmed_actionable_categories(catalog) - {observation_family(row["label"]) for row in effective}:
            raise ObservationBindingError("Scope request omitted a confirmed current observation",
                                          reason_code="scope_request_observation_omitted")
    ignored = []
    effective_identities = {(observation_family(row["label"]), row["subject"]) for row in effective}
    for row in rejected:
        informational_redundancy = row["label"] in _INFORMATIONAL_LABELS and bool(effective)
        repeated_current_identity = (
            row.get("reason_code") == "historical"
            and (observation_family(row["label"]), row["subject"]) in effective_identities
        )
        # Preserve a supported electrical danger even if the VLM also emitted
        # one unsupported cosmetic label from that same image.  This is not a
        # general invalid-field filter: the extra label remains in the raw and
        # ignored traces, and a cosmetic-only result still fails closed.
        noncritical_vision_cosmetic = (
            row["label"] == "表面剥落"
            and row["source"] == "vision"
            and row["reason"] == "Evidence does not support observable label"
            and output.category == "electrical"
            and output.risk == "electric"
            and any(
                accepted["source"] == "vision"
                and accepted["evidence_id"] == row["evidence_id"]
                and observation_family(accepted["label"]) in {"烧焦变色", "火花烧损"}
                for accepted in effective
            )
        )
        # "无法断电" survives as an explicit non-trusted trace entry when other
        # confirmed observations carry the analysis; the resident's statement
        # must not become a 不能供电 observation, and a limitation-only result
        # still fails closed.
        action_limitation = (
            row.get("reason_code") == "action_limitation_reported" and bool(effective)
        )
        if (not informational_redundancy and not repeated_current_identity
                and not noncritical_vision_cosmetic and not action_limitation):
            raise ObservationBindingError(str(row["reason"]),
                                          reason_code=str(row.get("reason_code") or "binding_invalid"))
        ignored.append({
            **row,
            "disposition": (
                "NOT_TRUSTED_ACTION_LIMITATION"
                if action_limitation
                else "NOT_TRUSTED_REDUNDANT_OBSERVATION"
            ),
            "ignored_reason": (
                "informational observation is not needed for the accepted current fault"
                if informational_redundancy
                else "unconfirmed or historical duplicate of an accepted current observation"
                if repeated_current_identity
                else "reported limitation on cutting power is not an outage observation"
                if action_limitation
                else "unsupported noncritical cosmetic observation did not change the accepted electrical danger"
            ),
        })
    # Deduplicate by danger category, not source id: repeated text/ASR/VLM
    # mentions must not consume the six model observation slots. This does not
    # prove completeness for multiple objects sharing the same danger label.
    if _confirmed_danger_categories(catalog) - {observation_family(row["label"]) for row in effective}:
        raise ObservationBindingError("Confirmed danger category was omitted",
                                      reason_code="confirmed_danger_omitted")
    identity_source = effective or normalized
    identity = sorted({
        (observation_family(row["label"]), row["subject"], row["quote"] if row["label"] in {"现象待核实", "人员行为观察", "范围外请求"} or row["subject"] == "未确定" else "")
        for row in identity_source
    })
    key = "evt2:" + sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]
    return key, {
        "contract": EVENT_CONTRACT,
        "observations": normalized,
        "effective_observations": effective,
        "informational_observations": [row for row in normalized if not row["effective"]],
        "ignored_observations": ignored,
        "adjusted_observations": adjusted,
        "effective_key": key,
        "raw_model_output": output.model_dump(mode="json"),
        **({"manual_review_reason": "OUT_OF_SCOPE_REQUEST"}
           if not effective and output.category == "other" and output.required_skill == "other"
           and output.risk == "none" and _scope_only_catalog(catalog) else {}),
    }


AI_ANALYSIS_READ = "ai_analysis:read"


AI_ANALYSIS_REVIEW = "ai_analysis:review"


DISPATCH_RECOMMENDATION_READ = "dispatch_recommendation:read"


INCIDENT_READ = "incident:read"


INCIDENT_MANAGE = "incident:manage"


ANNOUNCEMENT_READ = "announcement:read"


ANNOUNCEMENT_DRAFT = "announcement:draft"


ANNOUNCEMENT_PUBLISH = "announcement:publish"


WORK_ORDER_ANALYSIS_PROMPT_VERSION = "wo-analysis-v26-summary-fidelity"


WORK_ORDER_SUMMARY_INSTRUCTION = (
    "\nSUMMARY_EXPERIMENT: summary-groups-v2\n"
    "保留前述全部九字段规则。先依据原始证据和既有职责规则确定八个非summary字段，"
    "再组织summary；摘要分组不改变现象、职责、技能、风险或置信度判断依据。"
    "summary按有内容的类别写短句：本次；既往；否定/未知；行动/联系；系统处置。"
    "无依据的类别省略，总长不超过255字；压缩礼貌语和重复表达，不删必要事实。"
    "本次并列保留每个问题；只有原文明示因果才记录该关系，用户所述不等于现场确诊。"
    "既往保留本次原文明说的历史修复及此后是否复发，持续未复发不能改成没有提及。"
    "否定/未知须保留原文说不清的具体内容：不知道是什么对象、说不清什么现象、"
    "不知道来源、原因或两件事的关系，是不同信息，不得统一改成原因不明。"
    "行动/联系保留已经做过及明确没有做过的动作、联系时段/顺序和上门前提，"
    "不把请求写成已完成动作或已约定时间。"
    "系统处置仅在有范围外请求时记录前述V1边界：本系统未执行该请求，渠道待物业核实；"
    "这是本系统范围，不表示物业公司不办理。混合报修仍逐项保留范围外请求与实际故障，"
    "不能只保留其中一项，也不声称已派单、关闭工单或完成缴费。"
)


INCIDENT_SUMMARY_PROMPT_VERSION = "incident-summary-v1"


ANNOUNCEMENT_DRAFT_PROMPT_VERSION = "announcement-draft-v1"


DEFAULT_CONFIDENCE_THRESHOLD = 0.70


_CATEGORY_ALIASES = {
    "water_supply": WorkOrderCategory.PLUMBING,
    "plumbing": WorkOrderCategory.PLUMBING,
    "electrical": WorkOrderCategory.ELECTRICAL,
    "hvac": WorkOrderCategory.HVAC,
    "cleaning": WorkOrderCategory.CLEANING,
    "security": WorkOrderCategory.SECURITY,
    "other": WorkOrderCategory.OTHER,
    **{item.value.lower(): item for item in WorkOrderCategory},
    **{item.value: item for item in WorkOrderCategory},
}


_PRIORITY_ALIASES = {
    "p1": WorkOrderPriority.URGENT,
    "p2": WorkOrderPriority.HIGH,
    "p3": WorkOrderPriority.MEDIUM,
    "p4": WorkOrderPriority.LOW,
    **{item.value.lower(): item for item in WorkOrderPriority},
    **{item.value: item for item in WorkOrderPriority},
}


_SEVERITY_ALIASES = {
    "high": WorkOrderSeverity.HIGH,
    "medium": WorkOrderSeverity.MEDIUM,
    "low": WorkOrderSeverity.LOW,
    **{item.value: item for item in WorkOrderSeverity},
}


_SKILL_ALIASES = {
    "plumber": "PLUMBING",
    "plumbing": "PLUMBING",
    "electrician": "ELECTRICAL",
    "electrical": "ELECTRICAL",
    "hvac": "HVAC",
    "cleaning": "CLEANING",
    "security": "SECURITY",
    "maintenance": "MAINTENANCE",
}


SKILL_VALUE_NOT_NORMALIZED = "SKILL_VALUE_NOT_NORMALIZED"


_KNOWN_SKILL_CODES = frozenset(_SKILL_ALIASES.values()) | {"OTHER"}


def normalize_skill(raw: str) -> tuple[str, str]:
    """Return ``(dispatch skill code, resolution)`` for a model-provided label."""
    stripped = raw.strip()
    folded = stripped.casefold()
    aliased = _SKILL_ALIASES.get(folded)
    if aliased is not None:
        resolution = (
            SKILL_RESOLUTION_CANONICAL
            if aliased == stripped
            else SKILL_RESOLUTION_ALIASED
        )
        return aliased, resolution
    upper = stripped.upper()
    if upper in _KNOWN_SKILL_CODES:
        return upper, SKILL_RESOLUTION_CANONICAL
    return upper, SKILL_RESOLUTION_UNMAPPED


ORDINARY_MAINTENANCE_SKILLS = frozenset({"PLUMBING", "ELECTRICAL", "HVAC", "MAINTENANCE"})


UNCONSTRAINED_SKILLS = frozenset({"OTHER"})


SKILL_UNCONSTRAINED_OTHER = "SKILL_UNCONSTRAINED_OTHER"


def ordinary_maintenance(required_duty: str | None, required_skill: str | None) -> bool:
    return required_duty == "MAINTENANCE" and required_skill in ORDINARY_MAINTENANCE_SKILLS


def capability_note(*, required_duty: str | None, required_skill: str | None) -> str | None:
    """Explain a capability decision that opened the gate instead of closing it."""
    if required_duty is not None and required_skill in UNCONSTRAINED_SKILLS:
        return SKILL_UNCONSTRAINED_OTHER
    return None


def eligibility_denial_details(
    reason: str,
    *,
    required_duty: str | None = None,
    required_skill: str | None = None,
) -> dict[str, str]:
    """Machine-readable reason for EMPLOYEE_NOT_ELIGIBLE so the caller can recover."""
    details = {"reason": reason}
    if required_duty is not None:
        details["required_duty"] = required_duty
    if required_skill is not None:
        details["required_skill"] = required_skill
    note = capability_note(required_duty=required_duty, required_skill=required_skill)
    if note is not None:
        details["capability_note"] = note
    return details


_DUTY_FOR_CATEGORY = {
    WorkOrderCategory.PLUMBING: "MAINTENANCE",
    WorkOrderCategory.ELECTRICAL: "MAINTENANCE",
    WorkOrderCategory.HVAC: "MAINTENANCE",
    WorkOrderCategory.CLEANING: "CLEANING",
    WorkOrderCategory.SECURITY: "SECURITY",
    WorkOrderCategory.OTHER: "MAINTENANCE",
}


def category_duty(category: WorkOrderCategory) -> str:
    return _DUTY_FOR_CATEGORY[category]


def map_analysis_output(
    output: WorkOrderAnalysisOutput,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> MappedAnalysis:
    category = _CATEGORY_ALIASES.get(output.category) or _CATEGORY_ALIASES.get(
        output.category.casefold()
    )
    if category is None:
        raise ValueError("category is not a supported value")
    priority = _PRIORITY_ALIASES.get(output.priority) or _PRIORITY_ALIASES.get(
        output.priority.casefold()
    )
    if priority is None:
        raise ValueError("priority is not a supported value")
    severity = _SEVERITY_ALIASES.get(output.severity) or _SEVERITY_ALIASES.get(
        output.severity.casefold()
    )
    if severity is None:
        raise ValueError("severity is not a supported value")
    try:
        risk = AiRisk(output.risk.casefold())
    except ValueError as exc:
        raise ValueError("risk is not a supported value") from exc
    role = output.required_role.strip().upper()
    if role not in {"STAFF", "SUPERVISOR"}:
        raise ValueError("required_role is not a supported value")
    raw_skill, skill_resolution = normalize_skill(output.required_skill)
    skill = raw_skill
    # Existing category/duty catalogue uniquely defines ordinary cleaning and
    # security. Generic trade labels are redundant, not a second duty decision.
    # Unknown/custom restricted skills remain hard restrictions, never erased.
    if category in {WorkOrderCategory.CLEANING, WorkOrderCategory.SECURITY} and raw_skill in (
        ORDINARY_MAINTENANCE_SKILLS | {"CLEANING", "SECURITY"}
    ):
        skill = _DUTY_FOR_CATEGORY[category]
    low = output.confidence < threshold or (
        category is WorkOrderCategory.OTHER and skill != "MAINTENANCE"
    )
    return MappedAnalysis(
        category=category,
        subcategory=output.subcategory[:64],
        severity=severity,
        priority=priority,
        summary=output.summary[:255],
        risk=risk,
        required_role=role,
        required_skill=skill,
        required_duty=category_duty(category),
        confidence=output.confidence,
        low_confidence=low,
        raw_required_skill=output.required_skill,
        skill_mapping_applied=skill != raw_skill,
        skill_resolution=skill_resolution,
        raw_category=output.category,
        dispatch_fields_applied=not low,
    )


def capability_filter_reason(
    *, duty_codes: frozenset[str], skill_codes: frozenset[str],
    required_duty: str | None, required_skill: str | None,
) -> FilterReason | None:
    """Return the rejection reason, or None when the candidate passed the gate.

    Nothing new is filtered here: an unconstrained ``other`` still passes on
    purpose (PA-101-A). ``capability_note`` carries the explanation instead of a
    silent None.
    """
    if required_duty and required_duty not in duty_codes:
        return FilterReason.DUTY_MISMATCH
    if (
        required_skill
        and required_skill not in UNCONSTRAINED_SKILLS
        and not ordinary_maintenance(required_duty, required_skill)
        and required_skill not in skill_codes
    ):
        return FilterReason.SKILL_MISMATCH
    return None


def hard_filter_candidate(
    candidate: DispatchCandidate,
    *,
    required_skill: str | None,
    required_duty: str | None,
) -> FilterReason | None:
    if not candidate.employee_active:
        return FilterReason.NOT_ACTIVE
    if not candidate.user_active:
        return FilterReason.USER_NOT_ACTIVE
    if not candidate.property_effective:
        return FilterReason.PROPERTY_NOT_EFFECTIVE
    if not candidate.checked_in:
        return FilterReason.NOT_CHECKED_IN
    if not candidate.on_duty:
        return FilterReason.NOT_ON_DUTY
    if not candidate.has_staff_role:
        return FilterReason.NO_STAFF_ROLE
    capability_error = capability_filter_reason(
        duty_codes=candidate.duty_codes, skill_codes=candidate.skill_codes,
        required_duty=required_duty, required_skill=required_skill,
    )
    if capability_error is not None:
        return capability_error
    maintenance = ordinary_maintenance(required_duty, required_skill)
    if candidate.current_load >= candidate.max_capacity and not candidate.incident_follow:
        return FilterReason.CAPACITY_FULL
    if maintenance and candidate.current_load > 0 and not candidate.incident_follow:
        return FilterReason.BUSY
    return None


def score_candidate(
    candidate: DispatchCandidate,
    *,
    required_skill: str | None,
    required_duty: str | None,
    weights: DispatchWeights = DispatchWeights(),
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    duty_score = 1.0 if required_duty and required_duty in candidate.duty_codes else 0.0
    maintenance = ordinary_maintenance(required_duty, required_skill)
    skill_score = 1.0 if not maintenance and required_skill and required_skill in candidate.skill_codes else 0.0
    capacity = (
        (candidate.max_capacity - candidate.current_load) / candidate.max_capacity
        if candidate.max_capacity > 0
        else 0.0
    )
    building = 1.0 if candidate.same_building_history > 0 else 0.0
    response = (
        0.5
        if candidate.average_response_seconds is None
        else 1.0
        / (
            1.0
            + max(candidate.average_response_seconds, 0.0)
            / weights.response_reference_seconds
        )
    )
    completion = 0.5 if candidate.completion_rate is None else candidate.completion_rate
    rating = 0.5 if candidate.average_rating is None else candidate.average_rating / 5.0
    weighted = (
        weights.duty * duty_score
        + weights.skill * skill_score
        + weights.capacity * capacity
        + weights.same_building * building
        + weights.response * response
        + weights.completion * completion
        + weights.rating * rating
    )
    score = weighted / weights.total if weights.total > 0 else 0.0
    if duty_score:
        reasons.append("DUTY_MATCH")
    if skill_score:
        reasons.append("SKILL_MATCH")
    reasons.append("LOAD_REMAINING")
    if building:
        reasons.append("SAME_BUILDING_HISTORY")
    if candidate.average_response_seconds is not None:
        reasons.append("RESPONSE_HISTORY")
    if candidate.completion_rate is not None:
        reasons.append("COMPLETION_HISTORY")
    if candidate.average_rating is not None:
        reasons.append("RATING_HISTORY")
    if candidate.primary_incident_handler:
        reasons.append("PRIMARY_INCIDENT_HANDLER")
    if candidate.incident_follow:
        reasons.append("INCIDENT_FOLLOW")
    if maintenance:
        reasons.extend(("MAINTENANCE_GROUP", "GLOBAL_IDLE", "LEAST_RECENT_ASSIGNMENT"))
    return round(score, 4), tuple(reasons)


def rank_candidates(
    candidates: tuple[DispatchCandidate, ...],
    *,
    required_skill: str | None,
    required_duty: str | None,
    weights: DispatchWeights = DispatchWeights(),
) -> tuple[tuple[DispatchCandidate, float, tuple[str, ...]], ...]:
    eligible: list[tuple[DispatchCandidate, float, tuple[str, ...]]] = []
    for candidate in candidates:
        if hard_filter_candidate(
            candidate,
            required_skill=required_skill,
            required_duty=required_duty,
        ) is not None:
            continue
        score, reasons = score_candidate(
            candidate,
            required_skill=required_skill,
            required_duty=required_duty,
            weights=weights,
        )
        eligible.append((candidate, score, reasons))
    if ordinary_maintenance(required_duty, required_skill):
        # Assignment IDs are persistent chronological turn tickets, across all
        # service properties. ID is only the initial equal-history tie-break.
        eligible.sort(key=lambda item: (
            -int(item[0].incident_follow),
            item[0].last_assignment_id,
            item[0].employee_id,
        ))
        return tuple(eligible)
    eligible.sort(
        key=lambda item: (
            -int(item[0].primary_incident_handler),
            -item[1],
            item[0].current_load,
            -(
                item[0].check_in_at.timestamp()
                if item[0].check_in_at is not None
                else float("-inf")
            ),
            -item[0].same_building_history,
            item[0].employee_id,
        )
    )
    return tuple(eligible)


# PA-169 (t150): the quarantine must follow the resident, not the work order.
# Two reports of the same problem in the same words produce different work order
# ids (and therefore different ``input_hash`` values), so the identity used for
# "this text already produced an unusable answer" has to come from the content
# alone: width/case/punctuation-insensitive text plus the sorted attachment
# digests. Nothing about the work order, tenant or reporter goes in.
_FINGERPRINT_DROP = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_fingerprint_text(text: str | None) -> str:
    """Content form used by :func:`content_fingerprint`."""

    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _FINGERPRINT_DROP.sub("", folded)


def content_fingerprint(
    text: str | None,
    media_digests: Iterable[str] = (),
) -> str:
    """Stable identity of a report's content, independent of where it landed."""

    payload = json.dumps(
        {
            "text": normalize_fingerprint_text(text),
            "media": sorted({digest for digest in media_digests if digest}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()

