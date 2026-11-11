# AGENTS.md — 智慧物业 AI 运营与工单调度平台

本文件是后续所有 AI Agent / 工程师在本仓库工作的强制协议。
产品是 **Production V1**，不是 Demo。

## 1. 角色与边界

- 主 Codex 会话是唯一源码实施者与技术/产品交付负责人：负责规划、改代码、专项测试、页面操作、证据收集、保存本地 WIP、进度管理与交付准备。Cursor 仅作为编辑器和终端，不再作为实施 Agent。
- 需要单独复审时，复审 Codex 会话保持只读，并与主会话分离；复审必须覆盖需求遗漏、实现/页面/环境/证据核对、代码/安全/交互验收、问题分级与下一动作，不得修改工作区或递归创建更多审查者。主会话不得把同一工具内自检冒充独立复审。
- 用户负责业务目标、存在歧义的产品决策、费用/凭证/高风险环境操作及正式发布批准，以及最终实际使用验收。用户不负责逐轮搬运报告、挑选暂存名单、设计数据库字段或替 Codex 寻找原定功能遗漏。
- 实施者不是产品改需求的人。不擅自改变需求、技术栈、数据模型主键策略。
- 不为了“高级”而过度设计。禁止一次性生成整个项目后不做验证。
- 修改前先说明计划，禁止直接大范围重构。
- 每完成一个模块必须测试，包含失败路径，不只跑 Happy Path。
- 普通源码/页面/测试在已批准范围内自动推进；持久库迁移、冻结依赖升级、共享服务、付费调用、真实资料和正式发布仍须单独批准。

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
- Production V1 使用同步 SQLAlchemy Session + PyMySQL；数据库主导的 FastAPI Endpoint 优先使用普通 `def`，禁止在 `async def` 中直接执行同步数据库 IO。
- 事务边界属于 Service；Repository 只负责数据访问，不得在一个完整业务流程中各自随意 commit。
- 外部系统必须走 Provider：Sms / RealName / LLM / Vision / ASR / Storage / Notification。
- 每个 Provider 必须有 Development 实现，保证无企业凭证也能本地开发测试。
- 禁止把 API Key、密码、Secret 写入代码。只允许环境变量 / secret 文件。

## 4. 身份与数据铁律

- 核心表内部主键统一为 `BIGINT AUTO_INCREMENT`，外键使用与目标主键一致的 BIGINT；`users.id` 是内部永久用户身份，关联列命名为 `user_id`。
- 手机号、微信、密码、身份证都不是业务主键；安全不得依赖 ID 不可猜，必须依赖 RBAC、组织 Scope、Ownership Policy 与 Anti-IDOR。
- 一个自然人不得重复建号；支持绑定变更与账号合并。
- User 与 Employee 分离但关联。
- House 不得写死 `owner_name`。User 与 House 通过 `ResidentHouseRelation` 多对多，并保留历史。
- 所有租户数据必须带 `tenant_id`；小区级数据必须可按 `property_id` 隔离。
- 组织 Scope 仅为 `SYSTEM / TENANT / PROPERTY`；SELF 是资源所有权策略，不是 Scope Enum。
- `Employee` 归属 Tenant；通过带 `status / valid_from / valid_to` 的 `EmployeeProperty` 历史关系服务一个或多个 Property。
- 数据库时间戳保存 UTC；自然日业务使用按 Property 时区计算的 `business_date`，默认时区 `Asia/Shanghai`。
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
- 先小步实现，再测试并记录证据；批准范围内连续推进，只有需要新增授权、安全处置或达到停止条件时才暂停。
- 当前阶段未批准时，不得自行进入后续 Day 计划。
- 生产异常处理必须覆盖：超时、重试、校验失败、权限拒绝、并发冲突、幂等、外部依赖失败。
- 错误使用稳定 Error Code，日志带 `request_id`，结构化日志。
- 不提交 `.env`、密钥、真实业主数据、人脸/身份证影像。

## 8. 当前阶段

Day 1 / Day 2 基线已验收。Day 3 部署/可观测/备份与 Day 4 发布验证在本工作区执行。

2026-09-19 用户明确授权主 Codex 从当前实际进度接管普通源码、页面、专项测试、具名隔离合成环境验证、证据与交付准备，并连续推进冻结 Production V1。该授权不等于 Day 4、真实外部 Provider 或 Production V1 发布验收通过；新 Migration、冻结依赖升级、共享服务维护、付费资源、真实资料、上传提审和正式发布仍须单独批准。

当前续接 AUTH-IDENTITY-AI-001-C：正式认证进入业务、发布包隔离开发入口、自托管 AI 资源核对。密码策略已决定：最少 8 个字符，不强制大写，禁止对哈希输入做 casefold。

AI 默认部署：采用许可允许的开放权重 Qwen 等模型，在我们或企业控制的服务器上自托管（租用 GPU、自有机、托管机或私有云）。开发电脑只是可选验证设备，不是生产配置上限。业务后端可用 OpenAI-compatible HTTP 调用自有模型服务，这不等于调用第三方托管推理。未批准不得租机、购买硬件、开启计费实例或向第三方模型平台转发业务数据。

`learning/` 只解释当前实现与验证状态，**不能覆盖** `docs/` 正式工程规格、`AGENTS.md` 或 `.cursor/rules/`。运行时代码、种子数据、物业知识库导入源和生产镜像不得读取 `learning/`。验收证据放在 `reports/`。
