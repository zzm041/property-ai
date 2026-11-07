# 主管队列跟进建议

该功能帮助主管从当前待派/异常队列的一页中选择需要核实的工单，并列出已记录的依据及下一步建议。它不派单、不改状态、不联系住户，也不取代主管判断。

## 当前状态

已具备后端工具循环、独立 Provider 配置、接口、小程序入口和专项测试。早期真实模型/合成工具1/2以及后续V16两个场景通过的历史结果分别保留。V22的SQL_AGENT已完成真实本地模型3步queue→diagnose→finish、真实临时MySQL查询和认证ASGI组合验证；事实与SQL一致，选定业务表未改变，见 [V22实际窗口](../reports/semantic-v22-window-20261106/WINDOW-REVIEW.md)。这项证据独立于此前规划桩与合成工具，不拼接旧结果代替组合成功。

v26本地成套制品与四服务切换已通过独立复核，见 [本地切换](../reports/v26-cutover-20261106/REVIEW.md)。功能开关仍按原配置默认关闭；未因部署新源码自动启用Agent。微信真机、目标生产容量及正式发布验收仍未完成，本地组合和健康检查不能替代这些结论。

## 使用与边界

- 仅具有目标小区工单调度权限的用户可使用，入口是否展示由服务端能力接口决定。
- 一次读取队列前 5 条，最多诊断其中 3 张；不保证覆盖全部异常工单，不宣称最优排序。
- 模型只能选择“查询队列”“读取诊断”“结束”三种动作。每次操作重新鉴权，最终依据必须来自实际读取且复核未变化的工单记录。
- 每轮提供尚可调用的动作、诊断目标、可引用事实和剩余预算；结束最多选择 3 单、每单 1–4 条事实。提示帮助规划，服务端独立执行硬校验。
- 页面在切换小区、刷新、退出或发起业务变更时清除旧建议；版本不一致时提示刷新。
- 主管可通过“查看报修内容与内部记录”读取当前工单原始文字、语音转写、补充说明与处理日志，联系前核对原文限制；这些内容来自既有物业用途详情接口，不由Agent重新生成。详情失效时一并清除，见 [页面修正证据](../reports/supervisor-report-content-20261105/REVIEW.md)。
- 不可用时继续使用已有工单诊断及人工流程。开发模式标明演练结果，模型调用数为零。

## 接口

- `GET /api/v1/supervisor/work-order-agent/capabilities?property_id=<id>`：鉴权后返回小区 ID 与 enabled。
- `POST /api/v1/supervisor/work-order-agent`：输入 property_id、question；可带一致的 X-Property-Id。响应包含 status、items、property_id、request_id、execution_mode、planner_steps、model_calls 与简要 trace。
- items 中事实来自服务器诊断记录，模型仅选择工单和事实代码，不提供自由生成的处置指令。
- `AGENT.DISABLED` 为未启用；`AGENT.PROVIDER_FAILED`、`AGENT.INVALID_ACTION`、`AGENT.BUDGET_EXHAUSTED` 返回不可用且无部分建议；权限与版本错误沿用 HTTP 错误契约。

## 配置与运行验证

`SUPERVISOR_AGENT_ENABLED` 默认 false；`SUPERVISOR_AGENT_PROVIDER` 默认 development。真实运行还需独立设置 `SUPERVISOR_AGENT_BASE_URL`、`SUPERVISOR_AGENT_MODEL` 和私密 `SUPERVISOR_AGENT_API_KEY`，不能隐式借用工单分析或知识问答的模型窗口。

每次最多 7 步规划、输出上限 512 tokens/次。30 秒是检查点预算：同步数据库和 HTTP 返回后检查并丢弃迟到结果，不构成严格墙钟截止。多请求资源准入与部署容量仍须在运行验证中核对，不能仅凭单请求步数上限宣称生产限流已完善。

设置开关或模型连接前先完成具名隔离验证；本批未修改任何运行配置。服务端能力关闭时，小程序不展示入口。
