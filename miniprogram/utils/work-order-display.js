// Presentation only. Raw codes/IDs/UTC values still drive existing business actions.
const businessLabels = require("./business-labels");
const { formatBeijingTime } = require("./date-time");
const EVENT = {
  AI_TASK_ATTEMPT: "系统自动分析/恢复尝试",
  AI_TASK_FAILED: "自动处理暂时失败，请等待物业处理",
  CREATE: "提交报修", ANALYZE: "完成分析", ASSIGN: "安排处理人员", REASSIGN: "重新派单",
  ACCEPT: "员工接单", REJECT: "员工拒单", START: "开始处理", PROCESS_UPDATE: "更新处理进展",
  RECEIPT_RESOLVED: "提交处理回执", RECEIPT_UNRESOLVED: "提交未解决回执",
  RECEIPT_MEDIA_APPENDED: "补传回执照片",
  ESCALATE: "上报主管", DECIDE: "主管处理决定", CONFIRM: "住户确认解决",
  REOPEN: "反馈未解决，继续处理", CANCEL: "撤销工单", ARCHIVE: "归档",
  RATE: "服务评价", APPEND: "补充问题", SLA_WARNING: "时限预警",
  SLA_OVERDUE: "已超过处理时限", SLA_RESPONSE_TIMEOUT: "接单响应超时",
  AUTO_DISPATCH_WAIT: "等待可用人员，系统将自动继续安排",
};
const ACTOR = { RESIDENT: "住户", STAFF: "工作人员", SUPERVISOR: "主管", ADMIN: "管理员", SYSTEM: "系统" };
const PHASE = { REPORT: "报修附件", BEFORE: "处理前", AFTER: "处理后", ON_SITE: "现场附件" };
const KIND = { IMAGE: "图片", AUDIO: "语音" };
const CATEGORY = { PLUMBING: "供排水", ELECTRICAL: "电气", HVAC: "暖通", CLEANING: "保洁", SECURITY: "安保", OTHER: "其他" };

function label(values, code, fallback) {
  return Object.prototype.hasOwnProperty.call(values, code) ? values[code] : fallback;
}
function statusLabel(code) { return businessLabels.workOrderStatus(code); }
function utcLabel(value) {
  return formatBeijingTime(value);
}
function presentDetail(item) {
  const display = item.display || {};
  const names = display.assignee_names || {};
  const at = formatBeijingTime;
  const employee = id => {
    if (id == null) return "未记录处理人员";
    const name = Object.prototype.hasOwnProperty.call(names, id) ? names[id] : "";
    return name && name.trim() ? name : `员工编号 ${id}`;
  };
  const needsClarification =
    item.work_order && item.work_order.status === "PENDING_ANALYSIS" &&
    display.analysis_status === "LOW_CONFIDENCE";
  return {
    needsClarification,
    analysisMessage: needsClarification ? (display.analysis_message || "") : "",
    recoveryFailed: ["PENDING_ANALYSIS", "PENDING_ASSIGNMENT"].includes(item.work_order && item.work_order.status) &&
      (item.logs || []).filter(x => ["AI_TASK_ATTEMPT", "AI_TASK_FAILED"].includes(x.event_code)).sort((a, b) => b.id - a.id)[0]?.event_code === "AI_TASK_FAILED",
    timezoneLabel: display.property_timezone || "UTC",
    statusLabel: statusLabel(item.work_order && item.work_order.status),
    reasonLabel: businessLabels.workOrderReason(item.work_order && item.work_order.process_reason),
    createdAtLabel: at(item.work_order && item.work_order.created_at),
    assignments: (item.assignments || []).map(x => ({ ...x, assigneeLabel: employee(x.employee_id) })),
    processes: (item.processes || []).map(x => ({ ...x, assigneeLabel: employee(x.assignee_id), startedAtLabel: at(x.started_at) })),
    receipts: (item.receipts || []).map(x => ({ ...x, problemTypeLabel: label(CATEGORY, x.problem_type, "其他问题"), finishedAtLabel: at(x.finished_at) })),
    feedbacks: (item.feedbacks || []).map(x => ({ ...x, createdAtLabel: at(x.created_at) })),
    media: (item.media || []).map(x => ({ ...x, phaseLabel: label(PHASE, x.taken_phase, "附件阶段"), kindLabel: label(KIND, x.kind, "附件类型"), createdAtLabel: at(x.created_at) })),
    logs: (item.logs || []).map(x => ({ ...x,
      fromStatusLabel: x.from_status ? statusLabel(x.from_status) : "未创建",
      toStatusLabel: statusLabel(x.to_status), eventLabel: label(EVENT, x.event_code, "工单事件"),
      actorLabel: label(ACTOR, x.actor_type, "操作方"), createdAtLabel: at(x.created_at),
    })),
  };
}
function presentStaffHistory(item, currentUserId) {
  const diagnosticCodes = new Set(["AUTO_DISPATCH_WAIT", "AI_TASK_ATTEMPT"]);
  const businessLogs = [], systemLogs = [], groups = new Map();
  for (const row of presentDetail(item).logs.sort((a, b) => b.id - a.id)) {
    const log = { ...row, actorLabel: row.actor_user_id != null && row.actor_user_id === currentUserId
      ? "本人" : row.actor_type === "STAFF" ? "其他工作人员" : row.actorLabel };
    if (!diagnosticCodes.has(row.event_code)) { businessLogs.push(log); continue; }
    systemLogs.push(log);
    const group = groups.get(row.event_code) || { eventCode: row.event_code, label: row.eventLabel, count: 0, latestAtLabel: row.createdAtLabel };
    group.count += 1;
    groups.set(row.event_code, group);
  }
  return { businessLogs, systemLogs, systemGroups: [...groups.values()] };
}
module.exports = { statusLabel, utcLabel, presentDetail, presentStaffHistory };
