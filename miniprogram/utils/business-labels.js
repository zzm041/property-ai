function from(values, code, fallback) {
  return Object.prototype.hasOwnProperty.call(values, code)
    ? values[code]
    : fallback;
}

const WORK_ORDER_STATUS = {
  PENDING_ANALYSIS: "待分析",
  PENDING_ASSIGNMENT: "待安排人员",
  ASSIGNED: "已派单",
  ACCEPTED: "已接单",
  PROCESSING: "处理中",
  WAIT_CONFIRM: "待住户确认",
  COMPLETED: "已完成",
  CANCELLED: "已撤销",
  CLOSED: "已归档",
};

const WORK_ORDER_REASON = {
  NONE: "正常处理",
  WAITING_OWNER: "等待住户配合",
  WAITING_PARTS: "等待配件",
  INTERNAL_UNRESOLVED: "已上报，等待物业处理",
  ESCALATED: "已升级主管",
  EXTERNAL_COORDINATION: "物业协调处理中",
  REASSIGN_REQUIRED: "等待重新安排人员",
};

const IDENTITY_STATUS = {
  PENDING_REVIEW: "待双人审核",
  APPROVED: "审核通过，待本人确认",
  REJECTED: "已拒绝",
  EXPIRED: "已过期",
  COMPLETED: "已完成",
};

const IDENTITY_PURPOSE = {
  MERGE_SOURCE_PROOF: "证明待合并来源账号",
  ACCOUNT_RECOVERY: "高风险账号恢复",
  PHONE_REUSE: "新持有人号码复用",
};

const ANNOUNCEMENT_STATUS = {
  DRAFT: "草稿",
  PENDING_REVIEW: "待审核",
  REJECTED: "已驳回",
  PUBLISHED: "已发布",
  ARCHIVED: "已归档",
};

const ANNOUNCEMENT_REVIEW_DECISION = {
  APPROVE: "审核通过",
  REJECT: "审核驳回",
};

const ANNOUNCEMENT_HISTORY_ACTION = {
  ANNOUNCEMENT_DRAFT_CREATED: "建立草稿",
  ANNOUNCEMENT_UPDATED: "修改草稿",
  ANNOUNCEMENT_SUBMITTED: "提交审核",
  ANNOUNCEMENT_REJECTED: "审核驳回",
  ANNOUNCEMENT_PUBLISHED: "发布公告",
};

const KNOWLEDGE_STATUS = {
  DRAFT: "草稿",
  INGESTING: "导入中",
  ACTIVE: "已生效",
  FAILED: "导入失败",
  ARCHIVED: "已归档",
  PENDING: "等待处理",
  PROCESSING: "处理中",
  SUCCEEDED: "处理成功",
};

const RELATION_TYPE = {
  OWNER: "业主",
  TENANT: "租户",
  FAMILY: "家庭成员",
  OCCUPANT: "居住人",
};

const ON_DUTY = {
  ON_DUTY: "在岗",
  OFF_DUTY: "离岗",
  LEAVE: "请假",
};

const EMPLOYEE_STATUS = { ACTIVE: "在职", DISABLED: "已停用" };
const RELATION_STATUS = { PENDING: "待审核", ACTIVE: "有效", ENDED: "已结束", REJECTED: "已拒绝" };
const HOUSE_STATUS = { ACTIVE: "可用", INACTIVE: "停用" };
const KNOWLEDGE_TYPE = { FAQ: "常见问题", GUIDE: "办事指南", RULE: "规章制度", SOP: "作业指引", NOTICE: "通知资料" };
const KNOWLEDGE_VISIBILITY = { RESIDENT: "住户可见", STAFF_ONLY: "仅员工可见", PROPERTY: "本小区可见", TENANT: "企业范围可见" };

const DISPATCH_FILTER_REASON = {
  NOT_ACTIVE: "员工当前未启用",
  USER_NOT_ACTIVE: "员工账号当前不可用",
  PROPERTY_NOT_EFFECTIVE: "不在本小区有效服务期内",
  NOT_CHECKED_IN: "今日尚未签到",
  NOT_ON_DUTY: "当前不在岗",
  DUTY_MISMATCH: "职责不匹配",
  SKILL_MISMATCH: "特殊技能不匹配",
  CAPACITY_FULL: "当前任务容量已满",
  NO_STAFF_ROLE: "尚未认领员工身份",
  RECENTLY_REJECTED: "近期已拒绝该工单",
  BUSY: "当前已有任务占用",
};

function workOrderStatus(code) {
  return from(WORK_ORDER_STATUS, code, "未知状态");
}

function workOrderReason(code) {
  return from(WORK_ORDER_REASON, code, "处理情况待确认");
}

function identityStatus(code) {
  return from(IDENTITY_STATUS, code, "状态待确认");
}

function identityPurpose(code) {
  return from(IDENTITY_PURPOSE, code, "身份事项");
}

function announcementStatus(code) {
  return from(ANNOUNCEMENT_STATUS, code, "状态待确认");
}

function announcementReviewDecision(code) {
  return from(ANNOUNCEMENT_REVIEW_DECISION, code, "审核处理");
}

function announcementHistoryAction(code) {
  return from(ANNOUNCEMENT_HISTORY_ACTION, code, "公告更新");
}

function knowledgeStatus(code) {
  return from(KNOWLEDGE_STATUS, code, "状态待确认");
}

function relationType(code) {
  return from(RELATION_TYPE, code, "其他关系");
}

function onDutyStatus(code) {
  return from(ON_DUTY, code, "状态待确认");
}

function employeeStatus(code) {
  return from(EMPLOYEE_STATUS, code, "状态待确认");
}

function relationStatus(code) {
  return from(RELATION_STATUS, code, "状态待确认");
}

function houseStatus(code) {
  return from(HOUSE_STATUS, code, "状态待确认");
}

function knowledgeType(code) {
  return from(KNOWLEDGE_TYPE, code, "其他资料");
}

function knowledgeVisibility(code) {
  return from(KNOWLEDGE_VISIBILITY, code, "可见范围待确认");
}

function dispatchFilterReason(code) {
  return from(DISPATCH_FILTER_REASON, code, "当前不符合派单条件");
}

module.exports = {
  workOrderStatus,
  workOrderReason,
  identityStatus,
  identityPurpose,
  announcementStatus,
  announcementReviewDecision,
  announcementHistoryAction,
  knowledgeStatus,
  relationType,
  onDutyStatus,
  employeeStatus,
  relationStatus,
  houseStatus,
  knowledgeType,
  knowledgeVisibility,
  dispatchFilterReason,
};
