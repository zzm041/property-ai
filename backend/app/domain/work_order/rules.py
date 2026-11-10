"""Work order state machine, SLA windows, visibility and handling rules."""

from datetime import datetime, timedelta

from .models import (
    WorkOrderStatus,
    ProcessReason,
    WorkOrderEvent,
    WorkOrderPriority,
)


WORK_ORDER_CREATE = "work_order:create"


WORK_ORDER_READ = "work_order:read"


WORK_ORDER_DISPATCH = "work_order:dispatch"


WORK_ORDER_PROCESS = "work_order:process"


WORK_ORDER_RESOLVE = "work_order:resolve"


WORK_ORDER_DECIDE = "work_order:decide"


WORK_ORDER_CANCEL = "work_order:cancel"


SLA_POLICY_MANAGE = "sla:manage"


WAITING_PROCESS_REASONS = frozenset(
    {
        ProcessReason.WAITING_OWNER,
        ProcessReason.WAITING_PARTS,
        ProcessReason.INTERNAL_UNRESOLVED,
        ProcessReason.ESCALATED,
        ProcessReason.EXTERNAL_COORDINATION,
        ProcessReason.REASSIGN_REQUIRED,
    }
)


STAFF_MAY_COMPLETE_REASONS = frozenset({ProcessReason.NONE})


TERMINAL_STATUSES = frozenset(
    {
        WorkOrderStatus.COMPLETED,
        WorkOrderStatus.CANCELLED,
        WorkOrderStatus.CLOSED,
    }
)


PENDING_DISPATCH_STATUSES = frozenset(
    {
        WorkOrderStatus.PENDING_ANALYSIS,
        WorkOrderStatus.PENDING_ASSIGNMENT,
    }
)


ACTIVE_HANDLING_STATUSES = frozenset(
    {
        WorkOrderStatus.ASSIGNED,
        WorkOrderStatus.ACCEPTED,
        WorkOrderStatus.PROCESSING,
    }
)


DEFAULT_SLA_COMPLETE_MINUTES: dict[WorkOrderPriority, int] = {
    WorkOrderPriority.URGENT: 120,
    WorkOrderPriority.HIGH: 480,
    WorkOrderPriority.MEDIUM: 1440,
    WorkOrderPriority.LOW: 4320,
}


DEFAULT_SLA_RESPOND_MINUTES: dict[WorkOrderPriority, int] = {
    WorkOrderPriority.URGENT: 15,
    WorkOrderPriority.HIGH: 30,
    WorkOrderPriority.MEDIUM: 120,
    WorkOrderPriority.LOW: 480,
}


_LEGAL_TRANSITIONS: frozenset[tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent]] = (
    frozenset(
        {
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.ANALYZE,
            ),
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.ASSIGN,
            ),
            (
                WorkOrderStatus.PENDING_ANALYSIS,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderStatus.ASSIGNED,
                WorkOrderEvent.ASSIGN,
            ),
            (
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.ACCEPTED,
                WorkOrderEvent.ACCEPT,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REJECT,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.START,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.ACCEPTED,
                WorkOrderStatus.CANCELLED,
                WorkOrderEvent.CANCEL,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.PROCESS_UPDATE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.RECEIPT_UNRESOLVED,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.ESCALATE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.DECIDE,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderEvent.RECEIPT_RESOLVED,
            ),
            (
                WorkOrderStatus.PROCESSING,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REASSIGN,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.COMPLETED,
                WorkOrderEvent.CONFIRM,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.PROCESSING,
                WorkOrderEvent.REOPEN,
            ),
            (
                WorkOrderStatus.WAIT_CONFIRM,
                WorkOrderStatus.PENDING_ASSIGNMENT,
                WorkOrderEvent.REOPEN,
            ),
            (
                WorkOrderStatus.COMPLETED,
                WorkOrderStatus.CLOSED,
                WorkOrderEvent.ARCHIVE,
            ),
            (
                WorkOrderStatus.CANCELLED,
                WorkOrderStatus.CLOSED,
                WorkOrderEvent.ARCHIVE,
            ),
        }
    )
)


def is_legal_transition(
    *,
    from_status: WorkOrderStatus | str,
    to_status: WorkOrderStatus | str,
    event: WorkOrderEvent | str,
) -> bool:
    current = WorkOrderStatus(from_status)
    target = WorkOrderStatus(to_status)
    code = WorkOrderEvent(event)
    return (current, target, code) in _LEGAL_TRANSITIONS


def require_legal_transition(
    *,
    from_status: WorkOrderStatus | str,
    to_status: WorkOrderStatus | str,
    event: WorkOrderEvent | str,
) -> tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent]:
    current = WorkOrderStatus(from_status)
    target = WorkOrderStatus(to_status)
    code = WorkOrderEvent(event)
    if (current, target, code) not in _LEGAL_TRANSITIONS:
        raise ValueError(
            f"Illegal work-order transition {current.value} -> {target.value} "
            f"via {code.value}"
        )
    return current, target, code


def process_reason_for_status(
    *,
    status: WorkOrderStatus | str,
    process_reason: ProcessReason | str,
) -> ProcessReason:
    reason = ProcessReason(process_reason)
    current = WorkOrderStatus(status)
    if current is not WorkOrderStatus.PROCESSING:
        return ProcessReason.NONE
    return reason


def staff_may_submit_resolved_receipt(process_reason: ProcessReason | str) -> bool:
    return ProcessReason(process_reason) in STAFF_MAY_COMPLETE_REASONS


def sla_windows_for_priority(
    priority: WorkOrderPriority | str,
) -> tuple[int, int]:
    code = WorkOrderPriority(priority)
    return (
        DEFAULT_SLA_RESPOND_MINUTES[code],
        DEFAULT_SLA_COMPLETE_MINUTES[code],
    )


def compute_sla_due_at(
    *,
    created_at: datetime,
    priority: WorkOrderPriority | str,
    complete_minutes: int | None = None,
) -> datetime:
    minutes = complete_minutes
    if minutes is None:
        minutes = DEFAULT_SLA_COMPLETE_MINUTES[WorkOrderPriority(priority)]
    return created_at + timedelta(minutes=minutes)


def sla_warning_lead_minutes(complete_minutes: int) -> int:
    """Return a warning lead that never predates a positive SLA window."""

    if isinstance(complete_minutes, bool) or complete_minutes < 1:
        raise ValueError("complete_minutes must be a positive integer")
    return min(complete_minutes, max(complete_minutes // 4, 15))


def sla_is_overdue(*, due_at: datetime | None, now: datetime) -> bool:
    if due_at is None:
        return False
    return now > due_at


def employee_is_capacity_available(*, current_load: int, max_capacity: int) -> bool:
    return current_load < max_capacity


LEGAL_TRANSITION_MATRIX: tuple[
    tuple[WorkOrderStatus, WorkOrderStatus, WorkOrderEvent], ...
] = tuple(sorted(_LEGAL_TRANSITIONS, key=lambda item: (item[0], item[2], item[1])))


def resident_media_visible(*, phase: str, receipt_id: int | None, receipt_ids: set[int]) -> bool:
    return phase == "REPORT" or (phase in {"BEFORE", "AFTER"} and receipt_id in receipt_ids)


def resident_detail(data: dict) -> dict:
    """Build a new projection; never expose mixed historical notes."""
    def pick(item, fields):
        return {key: item[key] for key in fields.split() if key in item}

    reporter = data["work_order"]["reporter_user_id"]
    receipt_ids = {item["id"] for item in data["receipts"]}
    public_events = {event.value for event in WorkOrderEvent} | {"AI_TASK_FAILED", "AI_TASK_ATTEMPT"}
    logs, notes = [], []
    for item in data["logs"]:
        if item["event_code"] not in public_events:
            continue
        projected = pick(item, "id actor_type from_status to_status process_reason event_code created_at")
        if (item["actor_type"] == "RESIDENT" and item["actor_user_id"] == reporter
                and item["event_code"] in {"APPEND", "REOPEN", "CANCEL"}):
            projected["reason"] = item.get("reason")
            if item["event_code"] == "APPEND" and item.get("reason"):
                notes.append(item["reason"])
        logs.append(projected)
    result = {
        "work_order": pick(data["work_order"], "id tenant_id property_id house_id reporter_user_id status process_reason category severity priority version current_assignment_id sla_due_at assigned_at accepted_at started_at resolved_at closed_at created_at updated_at sla_overdue"),
        "text": data.get("text"), "asr_text": data.get("asr_text"),
        "extra_notes": "\n".join(notes) or None,
        "media": [pick(item, "id file_id kind taken_phase process_id receipt_id created_at")
                  for item in data["media"] if resident_media_visible(
                      phase=item["taken_phase"], receipt_id=item.get("receipt_id"), receipt_ids=receipt_ids)],
        "assignments": [
            pick(item, "id employee_id status assigned_at accept_at ended_at")
            | ({"reason": "AUTO_DISPATCH"} if item.get("reason") == "AUTO_DISPATCH"
               and item.get("assigned_by_user_id") is None else {})
            for item in data["assignments"]
        ],
        "processes": [pick(item, "id cycle_no assignee_id started_at ended_at") for item in data["processes"]],
        "receipts": [pick(item, "id process_id cycle_no assignee_id problem_type method materials fee_amount fee_flag resolved unresolved_reason follow_up finished_at") for item in data["receipts"]],
        "feedbacks": [pick(item, "id process_id confirmed rating comment unresolved_flag created_at")
                      for item in data["feedbacks"] if item["actor_user_id"] == reporter],
        "logs": logs,
        "display": pick(data["display"], "property_timezone assignee_names timestamp_labels analysis_status analysis_message"),
    }
    visible_times = set()

    def collect_times(value):
        if isinstance(value, list):
            for item in value:
                collect_times(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_at") and isinstance(item, str):
                    visible_times.add(item)
                elif isinstance(item, (dict, list)):
                    collect_times(item)

    collect_times({key: value for key, value in result.items() if key != "display"})
    result["display"]["timestamp_labels"] = {
        key: value for key, value in result["display"].get("timestamp_labels", {}).items()
        if key in visible_times
    }
    return result


HANDLING_LABELS = {'SELF': '本人独立处理', 'ASSISTED': '本人参与协作', 'EXTERNAL': '由支援人员处理'}


def normalize_handling(mode: str | None, note: str | None) -> tuple[str | None, str | None]:
    if mode is not None and (not isinstance(mode, str) or mode not in HANDLING_LABELS):
        raise ValueError('handling_mode is invalid')
    if note is not None and (not isinstance(note, str) or len(note) > 255):
        raise ValueError('handling_note must be at most 255 characters')
    cleaned = note.strip() or None if note is not None else None
    if mode is None and cleaned is not None:
        raise ValueError('handling_mode is required with handling_note')
    if mode in {'ASSISTED', 'EXTERNAL'} and cleaned is None:
        raise ValueError('handling_note is required for assisted or external work')
    return mode, cleaned


def handling_reason(mode: str | None, note: str | None) -> str | None:
    if mode is None:
        return None
    return f'实际处理（提交者自报）：{HANDLING_LABELS[mode]}' + (f'；{note}' if note else '')


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
