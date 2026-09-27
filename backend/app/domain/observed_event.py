"""V10 observable event evidence, not a new duty/risk or diagnosis policy.

Current-source quotes are validated before building the incident equality key.
Labels describe phenomena; there is deliberately no motive/root-cause label.
Original model output is retained separately in the scoped analysis audit.
"""
from hashlib import sha256
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EVENT_CONTRACT = 'observed-event-v1'
Label = Literal['滴渗水','不出水','不能供电','火花烧损','不制冷','噪声','杂物清扫',
                '敲门按铃','疑似燃气气味','烧焦变色','焦煳气味','松动','水渍','表面剥落',
                '外观无明显异常','现象待核实']
Subject = Literal['水龙头','排水接头','天花墙面','插座','照明','空调','扶手',
                  '楼道地面','房门','阀门管道','未确定']
Category = Literal['water_supply','electrical','hvac','cleaning','security','other']
Severity = Literal['low','medium','high']
Priority = Literal['p1','p2','p3','p4']
Risk = Literal['none','safety','flooding','electric','gas','other']
RequiredRole = Literal['STAFF']
RequiredSkill = Literal['maintenance','cleaning','security','other']

class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    label: Label
    subject: Subject = Field(description='必须由本项quote内的字面证据支持；quote未出现现有枚举主体时只能填未确定，不得借相邻句补全')
    source: Literal['report','asr','vision']
    quote: str = Field(min_length=2,max_length=160,description='当前来源中的唯一逐字证据；保留本项对象、否定、猜测和时态')

class ObservedAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    category: Category
    observations: list[Observation] = Field(min_length=1,max_length=6)
    severity: Severity
    priority: Priority
    summary: str = Field(min_length=1,max_length=255)
    risk: Risk
    required_role: RequiredRole
    required_skill: RequiredSkill
    confidence: float = Field(ge=0,le=1)


def work_order_analysis_response_format() -> dict[str, object]:
    """One canonical strict-output contract for provider and local adapter."""
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': 'work_order_analysis',
            'strict': True,
            'schema': ObservedAnalysisOutput.model_json_schema(),
        },
    }

PATTERNS = {
    '滴渗水':r'滴|渗水|漏水|喷水|喷出', '不出水':r'不出水|水都不出|没有水|没水|停水',
    '不能供电':r'没电|没有电|无电|不能供电|断电|停电|插座不能用',
    '火花烧损':r'火花|冒烟|起火|着火|明火|烧损',
    '不制冷':r'不[制变]?冷|不凉', '噪声':r'声|响', '杂物清扫':r'纸|纸盒|落叶|垃圾|杂物|清扫',
    '敲门按铃':r'敲门|拍门|按门铃|按铃', '疑似燃气气味':r'燃气.*(?:气味|异味)|(?:气味|异味).*燃气',
    '烧焦变色':r'烧(?:焦|黑)|焦黑|熏黑|发黑|黑色.*(?:痕|插孔)',
    '焦煳气味':r'焦(?:煳|糊)?味|烧焦味|糊味',
    '松动':r'松动|松了', '水渍':r'水渍|水迹|棕黄|黄褐|黄色.*斑',
    '表面剥落':r'剥落|脱落|起皮', '外观无明显异常':r'无.*(?:烧|损|烟|异常)|没有.*(?:损|烟|异常)|正常|完好',
    '现象待核实':r'不清楚|说不清|无法判断|不知道|没找到|不确定|未确定|待核实|可能|未见',
}
SUBJECT_PATTERNS = {
    '水龙头':r'水龙头|龙头', '排水接头':r'排水|接头|洗手盆', '天花墙面':r'天花|墙|涂层',
    '插座':r'插座|插孔', '照明':r'灯|照明', '空调':r'空调', '扶手':r'扶手',
    '楼道地面':r'楼道|角落|地面|通道', '房门':r'门', '阀门管道':r'阀|管|软管',
}
_UNCERTAIN = re.compile(r'猜|怀疑|可能|也许|好像|听说|据说|一人说|另一人说|有人说|他说|她说|转述|邻居说|家人说|对方说|无法判断|不知道|未确认|没.*确认')
_HISTORY = re.compile(r'以前|上次|历史|去年|早先|曾经')
_NEGATIVE = re.compile(r'(?:并非|不是|未见|没有|没|无|未)(?:正在|发生|明显|任何|出现|看到|发现|再)?[^，,。；;!?！？]{0,4}$')
_POST_NEGATIVE = re.compile(r'^[”’\"\s]*(?:并|从|始终|根本)?(?:没有|未|不曾|不存在)')

def _clause(text, start, end):
    left=max((m.end() for m in re.finditer(r'[，,。；;!?！？]',text[:start])),default=0)
    right=re.search(r'[，,。；;!?！？]',text[end:])
    return text[left:end+(right.start() if right else len(text)-end)],left

def bind_observations(output: ObservedAnalysisOutput, sources: dict[str,str]) -> tuple[str,dict]:
    normalized=[]
    for item in output.observations:
        source=sources.get(item.source,'') or ''
        positions=[m.start() for m in re.finditer(re.escape(item.quote),source)]
        if len(positions)!=1:raise ValueError('Observation needs one exact current-source span')
        start=positions[0];clause,left=_clause(source,start,start+len(item.quote))
        match=re.search(PATTERNS[item.label],item.quote)
        if not match:raise ValueError('Quote does not support observable label')
        # A token inside an explicitly denied/guessed/reported/historical clause
        # is not proof. Unknown stays unconfirmed; no motive label exists.
        if item.label!='现象待核实':
            if _UNCERTAIN.search(clause) or _HISTORY.search(clause):raise ValueError('Observation is not confirmed current evidence')
            prefix=source[left:start+match.start()]
            if item.label!='外观无明显异常' and _NEGATIVE.search(prefix):raise ValueError('Negated observation')
            suffix=source[start+match.end():left+len(clause)]
            if item.label!='外观无明显异常' and _POST_NEGATIVE.search(suffix):raise ValueError('Postposed negated observation')
        if item.subject!='未确定' and not re.search(SUBJECT_PATTERNS[item.subject],clause):
            raise ValueError('Subject not supported by the same observation clause')
        normalized.append({'label':item.label,'subject':item.subject,'source':item.source,
                           'quote':item.quote,'span':[start,start+len(item.quote)]})
    # Distinct phenomenon+object sets do not collapse to a broad trade/OTHER key.
    # Unconfirmed phenomena and confirmed phenomena with an unknown subject
    # retain the exact evidence.  Thus 未确定 is not a wildcard that merges
    # observations about two unidentifiable objects.  Known subjects keep the
    # canonical phenomenon+object identity so ordinary paraphrases still merge.
    identity=sorted({(r['label'],r['subject'],r['quote'] if r['label']=='现象待核实' or r['subject']=='未确定' else '') for r in normalized})
    key='evt1:'+sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:32]
    return key,{'contract':EVENT_CONTRACT,'observations':normalized,'effective_key':key,
                'raw_model_output':output.model_dump(mode='json')}
