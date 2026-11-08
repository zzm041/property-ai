const assert = require("assert");
const path = require("path");

const labels = require(path.resolve(__dirname, "../utils/business-labels"));

assert.strictEqual(labels.workOrderStatus("PROCESSING"), "处理中");
assert.strictEqual(labels.workOrderReason("WAITING_PARTS"), "等待配件");
assert.strictEqual(labels.identityStatus("PENDING_REVIEW"), "待双人审核");
assert.strictEqual(labels.identityPurpose("PHONE_REUSE"), "新持有人号码复用");
assert.strictEqual(labels.announcementStatus("PUBLISHED"), "已发布");
assert.strictEqual(labels.announcementReviewDecision("REJECT"), "审核驳回");
assert.strictEqual(labels.announcementHistoryAction("ANNOUNCEMENT_SUBMITTED"), "提交审核");
assert.strictEqual(labels.knowledgeStatus("INGESTING"), "导入中");
assert.strictEqual(labels.relationType("OWNER"), "业主");
assert.strictEqual(labels.onDutyStatus("ON_DUTY"), "在岗");
assert.strictEqual(labels.employeeStatus("ACTIVE"), "在职");
assert.strictEqual(labels.relationStatus("ENDED"), "已结束");
assert.strictEqual(labels.houseStatus("INACTIVE"), "停用");
assert.strictEqual(labels.knowledgeType("SOP"), "作业指引");
assert.strictEqual(labels.knowledgeVisibility("STAFF_ONLY"), "仅员工可见");
assert.strictEqual(labels.dispatchFilterReason("CAPACITY_FULL"), "当前任务容量已满");
assert.strictEqual(labels.dispatchFilterReason("UNRECOGNIZED"), "当前不符合派单条件");
assert.strictEqual(labels.workOrderStatus("NEW_CODE"), "未知状态");

console.log("business presentation label tests passed");
