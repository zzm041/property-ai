const { utcLabel } = require("./work-order-display");

// Presentation only: never rewrite stored text or infer events from partial matches.
const SYSTEM_TEMPLATES = [
  ["WORK_ORDER", "工单待确认", "Please confirm the work order result", "工单待确认", "请查看处理回执，并确认问题是否已解决"],
  ["WORK_ORDER", "工单 SLA 即将超时", "Work order is approaching its completion deadline", "工单处理时限预警", "工单即将超过完成时限，请及时处理"],
  ["WORK_ORDER", "工单 SLA 已超时", "Work order is overdue and requires escalation", "工单处理已超时", "工单已超过完成时限，需要升级处理"],
  ["WORK_ORDER", "Work order accept timeout", "The assignment timed out and was returned for automatic dispatch", "工单接单超时", "原派单已超时，工单已退回自动派单流程"],
  ["WORK_ORDER", "Work order response timeout", "The accepted work order has not started and needs supervisor review", "工单响应超时", "已接单工单尚未开始处理，需要主管跟进"],
  ["RESIDENT_BIND_REQUEST", "房屋绑定已通过", "Your house binding request was approved", "房屋绑定已通过", "房屋绑定申请已通过，请查看房屋关系"],
  ["RESIDENT_BIND_REQUEST", "房屋绑定未通过", "Your house binding request was rejected", "房屋绑定未通过", "房屋绑定申请未通过，请查看审核说明"],
];

function presentNotification(item) {
  const template = SYSTEM_TEMPLATES.find(row =>
    row[0] === item.resource_type && row[1] === item.title && row[2] === item.body
  );
  // The announcement title is authored text; retain it verbatim.
  const announcement = item.resource_type === "ANNOUNCEMENT" && item.body === "Announcement published";
  return Object.assign({}, item, {
    titleLabel: template ? template[3] : item.title,
    bodyLabel: template ? template[4] : announcement ? "公告已发布，请查看详情" : item.body,
    createdAtLabel: utcLabel(item.created_at),
    readAtLabel: item.read_at ? utcLabel(item.read_at) : "",
  });
}

module.exports = { presentNotification };
