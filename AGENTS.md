# AGENTS.md — 智慧物业 AI 运营与工单调度平台

本文件是后续所有 AI Agent / 工程师在本仓库工作的强制协议。
产品是 **Production V1**，不是 Demo。

## 1. 角色与边界

- 你是实现者，不是产品改需求的人。
- 项目负责人确认需求；Agent 负责可解释、可测试的工程实现。
- 不擅自改变需求、技术栈、数据模型主键策略。
- 不为了“高级”而过度设计。
- 禁止一次性生成整个项目后不做验证。
- 修改前先说明计划，禁止直接大范围重构。
- 每完成一个模块必须测试，包含失败路径，不只跑 Happy Path。

## 2. 冻结技术栈

不得未经负责人批准更换：

| 层 | 技术 |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic |
| DB | MySQL 8.0 |
| Cache / Lock | Redis |
| Async | RabbitMQ + Celery |
| RAG | Milvus + bge-m3 |
| AI | Qwen-family LLM / VLM + ASR Provider |
| Client | 微信原生小程序（单应用，按角色展示） |
| Deploy | Docker Compose + Nginx |

版本以 `backend/pyproject.toml` 与 `docs/09-DEPLOYMENT.md` 为准。

## 3. 架构约束

- 模块化单体 + Worker，禁止提前拆微服务。
- 禁止 Kubernetes、GraphRAG、多 Agent 编排。
- 目录分层：`api / domain / service / repository / model / schema / provider / worker / core / tests`。
- 外部系统必须走 Provider：Sms / RealName / LLM / Vision / ASR / Storage / Notification。
- 每个 Provider 必须有 Development 实现，保证无企业凭证也能本地开发测试。
- 禁止把 API Key、密码、Secret 写入代码。只允许环境变量 / secret 文件。

## 4. 身份与数据铁律

- 内部永久 `user_id` 才是用户身份。手机号、微信、密码都不是业务主键。
- 一个自然人不得重复建号；支持绑定变更与账号合并。
- User 与 Employee 分离但关联。
- House 不得写死 `owner_name`。User 与 House 通过 `ResidentHouseRelation` 多对多，并保留历史。
- 所有租户数据必须带 `tenant_id`；小区级数据必须可按 `property_id` 隔离。
- 不得自行保存和比对原始人脸特征。实名/活体走第三方 Provider。

## 5. 业务铁律

- 工单主状态保持稳定，PROCESSING 内特殊情况用 `process_reason`。
- 所有工单状态变化写 `WorkOrderLog`；安全与管理操作写 `AuditLog`。
- AI 无最终业务控制权。AI 挂掉时工单仍须创建成功，进入人工队列。
- 派单禁止让 LLM 直接点名员工；必须规则过滤 + 评分。
- 工作人员不能擅自结束异常工单或让业主自己找第三方。
- 公告必须人工审核后发布，禁止 AI 自动群发。
- RAG 召回必须带 `tenant_id` / `property_id` / `visibility` 过滤，并返回引用来源。
- SLA 必须可配置，禁止硬编码时限。

## 6. V1 明确不做

物业缴费、停车缴费、门禁、访客、社区商城、库存/财务/采购 ERP、IoT、AI 自动联系第三方、GraphRAG、多 Agent、K8s、微服务拆分。

## 7. 编码与提交纪律

- 先读 `docs/` 规格，再改代码。规格与代码冲突时，先指出冲突，再等确认。
- 先小步实现，再测试，再停下让负责人确认。
- 当前阶段未批准时，不得自行进入后续 Day 计划。
- 生产异常处理必须覆盖：超时、重试、校验失败、权限拒绝、并发冲突、幂等、外部依赖失败。
- 错误使用稳定 Error Code，日志带 `request_id`，结构化日志。
- 不提交 `.env`、密钥、真实业主数据、人脸/身份证影像。

## 8. 当前阶段

只执行已批准的阶段。阶段 0/1 产出是规格、目录骨架、架构方案，**不是**完整业务实现。
