# 批次 B：派单身份资格与员工查阅资格

主会话按 executing-plans/TDD 执行，最终独立只读复审。用户“继续实施执行”承接前轮授权；不重复请求普通修正批准，不提交/清理/覆盖 WIP。使用 writing-plans 明确本批契约。

## 目标、依据与范围

延续总设计 T04/T05。正式 `docs/05-AI-DISPATCH-SPEC.md` 4.1 要求账号正常、STAFF 或以上、当天签到、有效服务关系。现有人工 `_eligibility_error` 缺账号/角色/签到检查；现有 `_authorize_read` 对负责人缺员工状态、租户及有效服务关系检查，而 `get_contact` 已有这些限制。二者是既有安全约束不一致，不调整维修排序和业务主状态。

修改 `backend/app/service/work_order.py`；将现有 AUTO 的四种可派员工角色原值移到 `backend/app/domain/employee.py` 的共享常量，由 `backend/app/service/auto_dispatch.py` 和人工路径共用。不改 DB、API shape、依赖、服务、EKB，不调用真实 Provider。

1. 人工派单/改派和重开资格复核要求关联 user_id 存在、账号 ACTIVE、目标 tenant/property 范围有 STAFF/SUPERVISOR/PROPERTY_ADMIN/TENANT_ADMIN 之一、Property 时区当天有效签到；原员工、服务关系、在岗、容量检查保留。ignore_capacity 仅豁免容量，不豁免身份、签到。不改变人工派单的排序、容量上限、职责和技能政策；这些仍在 T05 继续验证。
2. 负责人读取他人工单时要求 Employee ACTIVE、租户匹配且当前服务关系有效；继承现有 WORK_ORDER_READ 与当前 Assignment 检查。服务关系失效必须阻止详情和通过工单授权的附件读取；附件返回既有 404，不读取 Storage。住户本人和合法主管规则不变。下班不等于失去查阅资格。
3. 附件上传者的既有所有权通道不在本批改变；双角色字段投影、历史上传者权限策略、混合 extra_notes 的拆分另列 OPEN，不能宣称全量隐私问题已修复。
4. 执行中发现同类列表旁路，纳入本批：`list_staff_work_orders` 拒绝禁用员工；根据有效服务小区和当前 READ Scope 求允许范围，传给 `repository/work_order.py:list_for_assignee`，在数据库 LIMIT 之前按 tenant/property 过滤，范围为空直接返回空列表。该 Repository 方法只有一个调用点。避免详情已拒绝但列表仍泄漏房屋/报修人等元数据。新增第四源码的原文与哈希，原三个目标和 WIP 基线不重写。

## 执行与验收

- [x] 保存原 WIP、前批源码、索引及本批四个目标的基线。
- [x] 新增 `backend/tests/unit/test_work_order_live_eligibility.py`：真实 Service 方法配模拟 Repository；失效账号/角色/签到、失效服务关系、跨租户与合法人员正例、ignore_capacity、当天时区、详情和附件拒绝读取；先运行失败测试。
- [x] 最小修正四个源码文件。共享角色集合保持原四值；不以角色缓存替代每次 Repository 读取。
- [x] 新专项及工单权限/IDOR/联系方式/重开/自动派单/员工相关回归通过。所有测试仅模拟数据与依赖，无真实库/服务。
- [x] 独立只读检查差异、测试和规格，返修问题；验证保护哈希、保存日志与剩余事项。

## 其余已确认观察与下一步

T04 字段投影：详情 `api/v1/work_orders.py` 直接返回 assignment.reason、log.payload_json.reason、content.extra_notes；`service/work_order.py` 的住户补充与员工反馈均追加同一 extra_notes，因此不能简单将整个字段删除或把全部文本视为公开。需要按来源确定历史展示策略、主管/员工/住户视图及直接附件访问，设计后再改；当前仅确认共享字段风险，未测试真实数据泄漏。

T05 职责/特殊技能和人工容量差异仍 OPEN；本批仅关闭账号/范围角色/签到缺口。T03/T06b 及业务容量政策继续总台账顺序。当前环境运行与真实模型不由本批重新验收。

## 本批完成记录

33 项专项通过，相关回归 295 项通过（包含前述 33 项）；独立复审另外复跑 33 项且未发现本批阻断。原有成果/索引 19 项保护哈希一致。证据位于 `reports/implementation-review-20261101-02/`。全部完成勾选仅对应本批，T04/T05 整体仍未关闭。
