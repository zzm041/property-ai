// Presentation only. Raw codes/IDs/UTC values still drive existing business actions.
const STATUS = {
  PENDING_ANALYSIS: "待分析", PENDING_ASSIGNMENT: "待安排人员", ASSIGNED: "已派单",
  ACCEPTED: "已接单", PROCESSING: "处理中", WAIT_CONFIRM: "待住户确认",
  COMPLETED: "已完成", CANCELLED: "已撤销", CLOSED: "已归档",
};
const REASON = {
  NONE: "正常处理", WAITING_OWNER: "等待住户配合", WAITING_PARTS: "等待配件",
  INTERNAL_UNRESOLVED: "已上报，等待物业处理", ESCALATED: "已升级主管",
  EXTERNAL_COORDINATION: "物业继续处理中", REASSIGN_REQUIRED: "等待重新安排人员",
};
const EVENT = {
  CREATE: "提交报修", ANALYZE: "完成分析", ASSIGN: "安排处理人员", REASSIGN: "重新派单",
  ACCEPT: "员工接单", REJECT: "员工拒单", START: "开始处理", PROCESS_UPDATE: "更新处理进展",
  RECEIPT_RESOLVED: "提交处理回执", RECEIPT_UNRESOLVED: "提交未解决回执",
  ESCALATE: "上报主管", DECIDE: "主管处理决定", CONFIRM: "住户确认解决",
  REOPEN: "反馈未解决，继续处理", CANCEL: "撤销工单", ARCHIVE: "归档",
  RATE: "服务评价", APPEND: "补充问题", SLA_WARNING: "时限预警",
  SLA_OVERDUE: "已超过处理时限", SLA_RESPONSE_TIMEOUT: "接单响应超时",
};
const ACTOR = { RESIDENT: "住户", STAFF: "工作人员", SUPERVISOR: "主管", ADMIN: "管理员", SYSTEM: "系统" };
const PHASE = { REPORT: "报修附件", BEFORE: "处理前", AFTER: "处理后", ON_SITE: "现场附件" };
const KIND = { IMAGE: "图片", AUDIO: "语音" };
const CATEGORY = { PLUMBING: "供排水", ELECTRICAL: "电气", HVAC: "暖通", CLEANING: "保洁", SECURITY: "安保", OTHER: "其他" };

function label(values, code, fallback) {
  return Object.prototype.hasOwnProperty.call(values, code) ? values[code] : `${fallback}（${code || "未提供"}）`;
}
function statusLabel(code) { return label(STATUS, code, "未知状态"); }
function utcLabel(value) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 19).replace("T", " ") + " UTC" : "时间不可用";
}
function presentDetail(item) {
  const display = item.display || {};
  const names = display.assignee_names || {};
  const times = display.timestamp_labels || {};
  const at = value => Object.prototype.hasOwnProperty.call(times, value) ? times[value] : utcLabel(value);
  const employee = id => {
    if (id == null) return "未记录处理人员";
    const name = Object.prototype.hasOwnProperty.call(names, id) ? names[id] : "";
    return name && name.trim() ? `${name}（员工 #${id}）` : `员工 #${id}（未提供展示名）`;
  };
  return {
    timezoneLabel: display.property_timezone || "UTC",
    statusLabel: statusLabel(item.work_order && item.work_order.status),
    reasonLabel: label(REASON, item.work_order && item.work_order.process_reason, "处理原因"),
    createdAtLabel: at(item.work_order && item.work_order.created_at),
    assignments: (item.assignments || []).map(x => ({ ...x, assigneeLabel: employee(x.employee_id) })),
    processes: (item.processes || []).map(x => ({ ...x, assigneeLabel: employee(x.assignee_id), startedAtLabel: at(x.started_at) })),
    receipts: (item.receipts || []).map(x => ({ ...x, problemTypeLabel: Object.prototype.hasOwnProperty.call(CATEGORY, x.problem_type) ? CATEGORY[x.problem_type] : x.problem_type, finishedAtLabel: at(x.finished_at) })),
    feedbacks: (item.feedbacks || []).map(x => ({ ...x, createdAtLabel: at(x.created_at) })),
    media: (item.media || []).map(x => ({ ...x, phaseLabel: label(PHASE, x.taken_phase, "附件阶段"), kindLabel: label(KIND, x.kind, "附件类型"), createdAtLabel: at(x.created_at) })),
    logs: (item.logs || []).map(x => ({ ...x,
      fromStatusLabel: x.from_status ? statusLabel(x.from_status) : "未创建",
      toStatusLabel: statusLabel(x.to_status), eventLabel: label(EVENT, x.event_code, "工单事件"),
      actorLabel: label(ACTOR, x.actor_type, "操作方"), createdAtLabel: at(x.created_at),
    })),
  };
}
module.exports = { statusLabel, utcLabel, presentDetail };
