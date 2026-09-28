"""Observable-event evidence bound to server-created source identifiers.

The model may select a bounded evidence id, but it cannot supply or relabel the
underlying source text. The server resolves the id back to the original report,
ASR transcript, or VLM observation before applying semantic guards.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

EVENT_CONTRACT = "observed-event-v2-source-id"
_INFORMATIONAL_LABELS = frozenset({"外观无明显异常", "现象待核实"})
Label = Literal["滴渗水", "不出水", "不能供电", "火花烧损", "不制冷", "噪声", "杂物清扫",
                "敲门按铃", "疑似燃气气味", "烧焦变色", "焦煳气味", "松动", "水渍", "表面剥落",
                "外观无明显异常", "现象待核实"]
Subject = Literal["水龙头", "排水接头", "天花墙面", "插座", "照明", "空调", "扶手",
                  "楼道地面", "房门", "阀门管道", "未确定"]
Category = Literal["water_supply", "electrical", "hvac", "cleaning", "security", "other"]
Severity = Literal["low", "medium", "high"]
Priority = Literal["p1", "p2", "p3", "p4"]
Risk = Literal["none", "safety", "flooding", "electric", "gas", "other"]
RequiredRole = Literal["STAFF"]
RequiredSkill = Literal["maintenance", "cleaning", "security", "other"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: Label
    subject: Subject = Field(description="必须由evidence_id对应原文支持；原文未出现枚举主体时填未确定")
    evidence_id: str = Field(
        pattern=r"^(?:report|asr|vision):[1-9][0-9]*$",
        description="只能引用INPUT_DATA.evidence_catalog中由服务端生成的当前证据ID",
    )


class ObservedAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Category
    observations: list[Observation] = Field(min_length=1, max_length=6)
    severity: Severity
    priority: Priority
    summary: str = Field(min_length=1, max_length=255)
    risk: Risk
    required_role: RequiredRole
    required_skill: RequiredSkill
    confidence: float = Field(ge=0, le=1)


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


def _source_segments(text: str | None) -> list[tuple[str, list[int]]]:
    """Split only on sentence boundaries and retain offsets in the source."""
    if not text:
        return []
    rows = []
    for match in re.finditer(r"[^。！？!?；;\n]+(?:[。！？!?；;]|$)", text):
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
    "滴渗水": r"滴|渗水|漏水|漏过|喷水|喷出|流出", "不出水": r"不出水|水[^，,。；;!?！？]{0,4}不出|没有水|没水|停水",
    "不能供电": r"没电|没有电|无电|不能供电|断电|停电|插座不能用",
    "火花烧损": r"火花|冒烟|起火|着火|明火|烧损",
    "不制冷": r"不[制变]?冷|不凉", "噪声": r"声|响", "杂物清扫": r"纸|纸盒|落叶|垃圾|杂物|清扫",
    "敲门按铃": r"敲门|拍门|按门铃|按铃", "疑似燃气气味": r"燃气.*(?:气味|异味)|(?:气味|异味).*燃气",
    "烧焦变色": r"烧(?:焦|黑)|焦黑|熏黑|发黑|黑色.*(?:痕|插孔)",
    "焦煳气味": r"焦(?:煳|糊)?味|烧焦味|糊味",
    "松动": r"松动|松了", "水渍": r"水渍|水迹|积水|棕黄|黄褐|黄色.*斑",
    "表面剥落": r"剥落|脱落|起皮|鼓起|掉.{0,4}(?:块|片)|掉漆|涂层.{0,8}(?:磨损|破损)|露出.{0,8}(?:金属|底色)",
    "外观无明显异常": r"无.*(?:烧|损|烟|异常|渗水|漏水|异味|积水|火花|焦味)|没有.*(?:损|烟|异常|渗水|漏水|异味|积水|火花|焦味)|正常|完好|未见明显",
    "现象待核实": r"不清楚|说不清|无法判断|无法确认|不知道|没找到|不确定|未确定|未确认|待核实|可能|怀疑|认为|声称|猜|未见|尚未|矛盾|一人说|另一人说|有人说|说法.{0,4}(?:矛盾|不一)|没有|没说|没查|不是|漏听|没.字",
}
SUBJECT_PATTERNS = {
    "水龙头": r"水龙头|龙头", "排水接头": r"排水|接头|洗手盆", "天花墙面": r"天花|墙|涂层",
    "插座": r"插座|插孔", "照明": r"灯|照明", "空调": r"空调", "扶手": r"扶手",
    "楼道地面": r"楼道|电梯厅|角落|地面|通道", "房门": r"门", "阀门管道": r"阀|管|软管",
}
_UNCERTAIN = re.compile(r"猜|怀疑|认为|声称|可能|也许|好像|听说|据说|一人说|另一人说|有人说|他说|她说|转述|邻居说|家人说|对方说|无法判断|无法确认|不知道|未确认|没.*确认|尚未")
_HISTORY = re.compile(r"以前|上次|历史|去年|早先|曾经|昨晚|上月|上个月")
_CURRENT_TIME = re.compile(r"今天|现在|目前|此刻|刚才|正在|又开始|仍在|还在|当下|本次")
_NEGATIVE = re.compile(r"(?:并非|不是|未见|没有|没|无|未)(?:正在|发生|明显|任何|出现|看到|发现|再)?[^，,。；;!?！？]{0,4}$")
_POST_NEGATIVE = re.compile(r"^[”’\"\s]*(?:并|从|始终|根本)?(?:没有|未|不曾|不存在)")
_LEADING_UNCERTAIN_SPEAKER = re.compile(
    r"^(?:我|本人|用户|住户|邻居|家人|对方|他|她|有人|一人|另一人|维修人员|工作人员)"
)
_SUBJECTLESS_CONTINUATION_PREFIX = {
    "不制冷": re.compile(r"^(?:出风|送风)(?:但|却|仍|仍然|还是)?$")
}
_GENERIC_SUBJECT_CONTINUATION = re.compile(
    r"^(?:现在|目前|此刻|刚才|随后|之后|仍|仍然|还|还在|正|正在|已经|已|尚未|不再|持续|"
    r"还在(?:缓慢|持续)|"
    r"水(?:正|一直|持续|不断)?|表面|外观|一人说|另一人说|有人说)$"
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


def _supported_subjects(text: str) -> set[str]:
    return {subject for subject, pattern in SUBJECT_PATTERNS.items() if re.search(pattern, text)}


def _previous_clause(text: str, clause_start: int) -> str:
    if clause_start <= 0:
        return ""
    end = clause_start - 1
    delimiters = "，,。；;!?！？"
    start = max((text.rfind(char, 0, end) for char in delimiters), default=-1)
    return text[start + 1:end].strip()


def _prior_clause_carries_unique_subject(
    text: str, clause_start: int, current_prefix: str, subject: str,
) -> bool:
    """Resolve a nearby omitted Chinese subject without crossing sentences.

    The continuation must follow a comma/semicolon, contain no competing
    catalogued subject, and the immediately preceding clause must contain the
    one requested subject without hearsay or historical qualification.
    """
    if clause_start <= 0 or text[clause_start - 1] not in "，,；;":
        return False
    compact = re.sub(r"\s+", "", current_prefix)
    if not compact or _GENERIC_SUBJECT_CONTINUATION.fullmatch(compact) is None:
        return False
    prior = _previous_clause(text, clause_start)
    return (
        _supported_subjects(prior) == {subject}
        and not _UNCERTAIN.search(prior)
        and not _HISTORY.search(prior)
        and not _NEGATIVE.search(prior)
        and not _POST_NEGATIVE.search(prior)
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


def bind_observations(
    output: ObservedAnalysisOutput, catalog: Mapping[str, Mapping[str, object]],
) -> tuple[str, dict[str, object]]:
    normalized = []
    rejected = []
    for item in output.observations:
        evidence = catalog.get(item.evidence_id)
        if evidence is None:
            raise ValueError("Observation evidence id is not in the current catalog")
        quote = str(evidence.get("text") or "")
        source = str(evidence.get("source") or "")
        if not quote or item.evidence_id.split(":", 1)[0] != source:
            raise ValueError("Observation evidence catalog is invalid")
        rejection_kind = None
        try:
            matches = list(re.finditer(PATTERNS[item.label], quote))
            if not matches:
                raise ValueError("Evidence does not support observable label")
            selected_clause = quote
            selected_clause_start = 0
            if item.label != "现象待核实":
                confirmed = []
                postposed_negative = False
                historical = False
                uncertain = False
                prefixed_negative = False
                for candidate in matches:
                    clause, clause_start = _clause_for_match(quote, candidate)
                    if _UNCERTAIN.search(clause):
                        uncertain = True
                        continue
                    if _HISTORY.search(clause):
                        historical = True
                        continue
                    local_start = candidate.start() - clause_start
                    local_end = candidate.end() - clause_start
                    prefix = clause[:local_start]
                    suffix = clause[local_end:]
                    if item.label != "外观无明显异常" and _NEGATIVE.search(prefix):
                        prefixed_negative = True
                        continue
                    if item.label != "外观无明显异常" and _POST_NEGATIVE.search(suffix):
                        postposed_negative = True
                        continue
                    confirmed.append((candidate, clause, clause_start))
                if not confirmed and postposed_negative:
                    rejection_kind = "postposed_negative"
                    raise ValueError("Postposed negated observation")
                if not confirmed and prefixed_negative:
                    rejection_kind = "prefixed_negative"
                    raise ValueError("Negated observation")
                if not confirmed and uncertain:
                    rejection_kind = "uncertain"
                    raise ValueError("Observation is not confirmed current evidence")
                if not confirmed and historical:
                    rejection_kind = "historical"
                    raise ValueError("Observation is not confirmed current evidence")
                if not confirmed:
                    raise ValueError("Observation is not confirmed current evidence")
                match, selected_clause, selected_clause_start = confirmed[0]
            else:
                match = matches[0]
                selected_clause, selected_clause_start = _clause_for_match(quote, match)
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
                    and _supported_subjects(prior_clause) == {item.subject}
                    and not _UNCERTAIN.search(prior_clause)
                    and not _HISTORY.search(prior_clause)
                    and not _NEGATIVE.search(prior_clause)
                    and not _POST_NEGATIVE.search(prior_clause)
                )
                unique_subject_continuation = _prior_clause_carries_unique_subject(
                    quote, selected_clause_start, prefix or match.group(0), item.subject,
                )
                if clause_subjects or not (explicit_continuation or unique_subject_continuation):
                    rejection_kind = "subject_unsupported"
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
            and output.category == "other"
            and output.required_skill == "other"
            and output.confidence < 0.7
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
    ignored = []
    effective_identities = {(row["label"], row["subject"]) for row in effective}
    for row in rejected:
        informational_redundancy = row["label"] in _INFORMATIONAL_LABELS and bool(effective)
        repeated_current_identity = (
            row.get("reason_code") == "historical"
            and (row["label"], row["subject"]) in effective_identities
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
                and accepted["label"] in {"烧焦变色", "火花烧损"}
                for accepted in effective
            )
        )
        if not informational_redundancy and not repeated_current_identity and not noncritical_vision_cosmetic:
            raise ValueError(str(row["reason"]))
        ignored.append({
            **row,
            "disposition": "NOT_TRUSTED_REDUNDANT_OBSERVATION",
            "ignored_reason": (
                "informational observation is not needed for the accepted current fault"
                if informational_redundancy
                else "unconfirmed or historical duplicate of an accepted current observation"
                if repeated_current_identity
                else "unsupported noncritical cosmetic observation did not change the accepted electrical danger"
            ),
        })
    identity_source = effective or normalized
    identity = sorted({
        (row["label"], row["subject"], row["quote"] if row["label"] == "现象待核实" or row["subject"] == "未确定" else "")
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
    }
