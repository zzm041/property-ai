"""Bounded supervisor guidance from recorded facts, never dispatch decisions."""

_STATUS = {
    'PENDING_ANALYSIS': ('工单等待分析或人工核实。', '检查报修信息是否齐全；分析不可用时通过现有人工流程处理。'),
    'PENDING_ASSIGNMENT': ('工单尚未派出。', '重新核实人员当前在岗、职责、特殊技能和容量，再决定安排；历史记录不能代替当前资格。'),
    'ASSIGNED': ('已派单，等待员工接单。', '核实接单情况，必要时按现有拒单或改派流程处理。'),
    'ACCEPTED': ('员工已接单，尚未开始处理。', '确认到场安排；如无法履约，由主管按现有流程协调。'),
    'PROCESSING': ('工单正在处理。', '核实处理进展，并在现有进展或主管决定中记录下一步。'),
    'WAIT_CONFIRM': ('员工已提交处理回执，等待住户确认。', '核实住户反馈，再按既有确认或返修流程处理。'),
    'COMPLETED': ('工单已完成。', '按既有归档与查询流程保留证据。'),
    'CLOSED': ('工单已归档。', '查看历史回执和反馈，不把本条诊断当作重新开工指令。'),
    'CANCELLED': ('工单已撤销。', '保留撤销与处理历史，不再安排现场处理。'),
}
_WAIT = {
    'WAITING_IDLE': '最近一次派单尝试记录为等待空闲人员；这不是当前员工容量。',
    'WAITING_ELIGIBLE': '最近一次派单尝试未找到合格候选；当前资格需重新核实。',
    'INVALID_STORED_ANALYSIS': '最近一次派单尝试发现分析结果无法用于派单。',
}
_PROCESS = {
    'WAITING_PARTS': '确认配件到位安排，记录当前负责人、沟通进展和下一步。',
    'WAITING_OWNER': '联系住户确认可配合时间，并记录后续安排。',
    'EXTERNAL_COORDINATION': '按需人工联系支援或第三方，记录安排与结果；物业保留跟进责任。',
    'INTERNAL_UNRESOLVED': '主管核实未解决原因，通过现有异常决定流程明确处理安排。',
    'ESCALATED': '主管核实已上报问题，明确处理方案、负责人和下一步。',
    'REASSIGN_REQUIRED': '主管核实原因后重新检查候选资格，再通过现有入口改派。',
}


def diagnosis_items(*, status, process_reason, analysis_status, cycle_no, wait_reason, overdue,
                    analysis_error_code=None):
    """Return fixed text and source categories; no resident or employee text."""
    text, action = _STATUS.get(status, ('工单状态未识别。', '刷新并核对工单状态后处理。'))
    facts = [('CURRENT_STATE', text, 'work_order')]
    actions = [action]
    scope_review = (status == 'PENDING_ANALYSIS' and analysis_status == 'LOW_CONFIDENCE'
                    and analysis_error_code == 'AI.OUT_OF_SCOPE')
    if scope_review:
        facts.append(('OUT_OF_SCOPE_REVIEW_REQUIRED', '当前记录为范围外请求，等待人工核实。', 'analysis'))
        actions = ['核对原始请求并向住户说明办理渠道；不代为执行范围外业务，不要求补充不存在的故障。']
    if status in {'PENDING_ANALYSIS', 'PENDING_ASSIGNMENT'}:
        if analysis_status in {'FAILED', 'LOW_CONFIDENCE'} and not scope_review:
            facts.append(('ANALYSIS_REVIEW_REQUIRED', '分析失败或置信不足，需要人工核实。', 'analysis'))
        elif analysis_status is None:
            facts.append(('ANALYSIS_UNKNOWN', '尚无可核对的分析记录；不能据此判断模型已经失败。', 'work_order'))
        if status == 'PENDING_ASSIGNMENT' and wait_reason in _WAIT:
            facts.append((wait_reason, _WAIT[wait_reason], 'work_order_log'))
    if type(cycle_no) is int and cycle_no > 1:
        facts.append(('MULTIPLE_PROCESS_ROUNDS', f'当前为第{cycle_no}轮处理；处理轮次不等同于返修次数或员工责任。', 'work_order_process'))
    if status == 'PROCESSING' and process_reason in _PROCESS:
        actions.append(_PROCESS[process_reason])
    if overdue and status not in {'COMPLETED', 'CLOSED', 'CANCELLED'}:
        facts.append(('SLA_OVERDUE', '当前工单已超过记录的SLA到期时间。', 'work_order'))
        actions.append('核实延误原因并联系相关人员，记录处理安排；不要仅为消除超时标记而关闭工单。')
    return facts, actions
