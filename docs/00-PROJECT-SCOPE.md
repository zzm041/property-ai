# 00 — 项目范围

**产品名称：** 智慧物业 AI 运营与工单调度平台  
**产品类型：** 面向物业企业的 Production V1 产品规格

**文档状态：** 冻结范围；业务已实现并有本地工程验收记录，不表示正式发布或商业上线

**主客户端：** 一个微信原生小程序，按角色展示不同功能

## 1. 一句话定位

帮助物业企业在多个小区内，用同一套系统完成住户报修、人员排班状态、工单调度、异常升级、公告与制度问答，而不是做社区超级 App。

## 2. 服务对象与规模

- 服务多个小区（多 `Property`），归属一个或多个物业企业（`Tenant`）。
- V1 目标量级：数千级业主、数十到数百名物业工作人员。
- 最终部署形态：微信小程序 + Docker Compose 后端。

## 3. V1 必须交付

1. 身份与账户（微信 / 短信 / 密码，账号合并与高风险恢复）
2. 物业、房屋、住户档案与绑定审核
3. 物业人员、签到、在岗状态、负载
4. 工单全生命周期（报修、派单、处理、回执、评价、重开）
5. 稳定工单状态机 + 全量日志
6. AI 工单理解（失败可降级）
7. 规则引擎智能派单（可人工接管）
8. 集中事件（Incident）聚合，避免重复派工
9. 公告（可 AI 起草，必须人工审核发布）
10. 物业知识助手（RAG，带引用和租户隔离）
11. 可配置 SLA
12. 通知（站内 + 微信订阅消息 Provider 预留）
13. 系统治理：RBAC、审计、隔离、日志、健康检查、迁移、备份、幂等

## 4. V1 明确不做

以下内容进入 `docs/BACKLOG.md`，禁止擅自实现：

- 物业缴费 / 停车缴费
- 门禁系统 / 访客系统
- 社区商城
- 完整库存 ERP / 财务 ERP / 采购系统
- IoT 平台
- AI 自动联系第三方服务商
- GraphRAG / 多 Agent 物业系统
- Kubernetes / 微服务拆分

第三方维修协调是物业内部动作，系统只记录“物业已联系外部专业服务”，不向业主暴露第三方下单入口。

## 5. 架构原则（本期冻结）

- **模块化单体 + Worker**，一个 FastAPI 进程 + Celery Worker。
- 外部依赖全部 Provider 化，Development / Production 解耦。
- AI 是辅助决策，不是控制面。
- 数据所有权以 `tenant_id` 为第一隔离边界，`property_id` 为小区边界。

## 6. 历史阶段划分

以下保留初始计划用于追溯，其中逐 Task 确认要求是当时约定，不是当前开发限制。Day 1 / Day 2 基线已验收；后续本地交付记录与正式发布验收须分别核对。当前实施边界以仓库协议和最新授权为准。

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| 0 / 1 | 范围、规格、架构、目录骨架 | 已完成 |
| 0.5 | Frozen Specification Reconciliation：统一旧规格与最新工程决策 | 已完成 |
| Day 1 | 按 Task 1~8 完成基础设施到小程序纵向链路及完整验收 | 基线已验收 |
| Day 2 | 后续 Production V1 模块 | 基线已验收 |
| Day 3 / Day 4 | 部署、可观测、备份与发布验证 | 已有本地交付记录；不等于正式发布验收通过 |

旧的“Day 1 仅做基础设施、身份/房产留到 Day 2+”节奏正式废弃。新的 Day 1 固定拆分：

1. Task 1：Environment / FastAPI Core / Docker / Health / Test Baseline
2. Task 2：Database Foundation + Tenant / Property / Building / Unit / House
3. Task 3：User / UserRole / Development Auth / RBAC / AuditLog Foundation / 基础 Dev Seed
4. Task 4：ResidentBindRequest / ResidentHouseRelation / Approval / Reject / Audit
5. Task 5：Employee / Department / Duty / Skill / EmployeeProperty / 多 Property / Dev Seed
6. Task 6：AttendanceRecord / EmployeeDailyStatus / business_date / Attendance Seed
7. Task 7：微信原生小程序最小纵向业务链路
8. Task 8：完整 Integration / Security / Migration / Acceptance

历史 Day 1 范围不包含 Milvus、RAG、LLM、VLM、ASR、WorkOrder、AI 派单、Incident、SLA、正式微信/短信/实名或 Production Nginx。这一早期范围不表示这些能力目前尚未实现。

## 7. 成功标准（产品层）

- 住户能完成报修并看到真实进度，而不是“提交成功”假页面。
- 物业能按当天签到和负载派单，而不是从员工名单里让 LLM 抽签。
- AI 宕机时，工单仍能进人工队列。
- 主管能处理异常工单，业主不会被要求自己找第三方。
- 公告必须人审；知识问答不会串到别的小区制度。
