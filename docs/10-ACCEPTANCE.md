# 10 — 验收标准

验收分为：**Stage 0.5 规格统一验收**、**Day 1 纵向链路验收**与 **Production V1 产品验收**。未完成前置层不得宣称后续层可交付。

## A. Stage 0.5 规格统一验收

1. 仓库具备 Git 忽略规则、README、AGENTS.md、`.cursor/rules/project.mdc`。
2. `docs/00` 至 `docs/10` 与 `BACKLOG.md` 存在且覆盖需求原文中的范围、角色、状态机、AI 派单、安全、技术栈冻结。
3. 推荐目录已创建，分层名称与规格一致。
4. 未实现完整业务模块；无假登录、无连真实外部 API 的硬编码密钥。
5. 版本冻结表与 `backend/pyproject.toml` 一致。
6. V1 明确不做的能力未出现在目录规划里（无 payment/iot/k8s 包）。
7. 所有核心表主键统一为 BIGINT AUTO_INCREMENT、外键同型；不存在 UUID/CHAR(36) 的现行主键规则。
8. Unit、ResidentHouseRelation、EmployeeProperty、Attendance、组织 Scope、同步 Session 与新 Day 1 Task 1~8 在所有冻结文档中语义一致。
9. `.python-version` 与 Windows Host Development Runtime 为 3.12.10，项目兼容范围为 `>=3.12,<3.13`；Production Docker Runtime 在 Deployment 阶段独立验证并冻结明确 tag/digest。
10. Stage 0.5 不创建 Route、ORM、Service、Repository、Migration 或小程序业务页面；完成后重新生成 Day 1 计划并等待确认。

## A2. Day 1 验收（Task 8 汇总）

- `healthz` 成功；`readyz` 分别报告 MySQL、Redis、RabbitMQ，Critical/Degraded 语义符合非功能规格。
- Windows Host 与项目 `.venv` 均为 Python 3.12.10；metadata 允许该版本，依赖可安装，pytest 与 FastAPI 可运行，且不混用全局包。
- Development Auth 成功；未认证请求拒绝；角色与 Scope 从数据库读取。
- `APP_ENV=production` 且启用 Development Auth 时被安全保护阻止；production 执行 Dev Seed 被拒绝。
- Resident 无法调用 Manager API；Cross Tenant、Cross Property、IDOR 与篡改 `property_id` 均被拒绝。
- Property 层级校验真实落在 MySQL 8；非法 Building/Unit/House 组合被约束或 Service 拒绝。
- ResidentBindRequest 提交、批准、拒绝成功；重复批准、重复有效关系和未授权批准被拒绝；批准/拒绝产生 AuditLog。
- Employee 与 User 正确关联；一个 Employee 支持多个带生命周期的 EmployeeProperty。
- 无当前有效 EmployeeProperty 时不能向目标 Property 签到。
- 签到使用 `business_date`；`check_in_at/check_out_at` 存 UTC；同日冲突签到被阻止。
- AttendanceRecord 与 EmployeeDailyStatus 在同一事务原子成功或回滚。
- Integration Test 使用与 Development DB 隔离的 MySQL 8 Test DB，不使用 SQLite。
- Migration 在明确可销毁的 Test DB 完成 upgrade → downgrade → re-upgrade；禁止对 Development DB 做 downgrade。
- Validation Error 使用统一 Error Contract。
- Task 3/5/6 分阶段 Seed 不提前引用未建立实体；Task 8 验证完整 Seed。

## B. Production V1 分模块验收（后续执行时使用）

### B1 身份

- 三种登录均可在 Development Provider 下测通。
- 同一手机号/同一微信不能建两个 User。
- 改密、忘记密码、换手机、换微信、合并均写审计。
- 实名恢复不落人脸特征；Provider 失败进入人工审核。
- 无凭证时测试套件仍绿。

### B2 房产与住户

- 物业可建楼栋房屋；House 无 owner_name 业务字段。
- 住户申请绑定 → 自动或人工审核 → `ResidentHouseRelation`。
- 退租后历史关系可查，当前关系正确。

### B3 员工与派单

- 未签到、离岗、错小区、错职责、满负载人员不会被自动派单。
- 派单按目标时点有效的 `EmployeeProperty` + `EmployeeDailyStatus` + Duty + Skill 筛选；历史失效关系不得命中。
- LLM 不直接指定员工。
- 支持人工派单、改派、拒单、超时重派。
- 派单理由可追溯。

2026-09-24 负责人澄清：上述安全与过滤条件之外，正常且信息足够、存在合格人员的报修必须实际自动完成理解→规则选人→AUTO Assignment→通知，不经过物业逐单分析/选人/确认。人工后备比例不能替代自动化验收。

后续真实模型/业务联合验收须同时提供以下证据，不以 JSON 合格率或模型自报 confidence 代替：

1. 代表性日常文字、图片、语音报修是否正确理解可观察现象；危险事实、当前/历史、否定与权限边界单独核对。
2. 实际同一工单是否经独立 Worker、实时任职/技能/签到/负载过滤、确定性评分，自动写入 Assignment 并生成通知；保存候选与未选原因。
3. 知识回答是否来自本次授权检索的文档/版本/引用，无命中不编造；基础模型无需预知小区制度。
4. 事先定义正常任务集合、既定例外及人员可用条件，分别记录实际 AUTO、必要后备、不必要后备、错误分流和未验证；不把无候选、模型失败与政策未定混为同一原因，不用本次对抗性小集倒算真实业务后备率。
5. 按非功能规格分别记录创建、排队、模型分析、派单/通知、问答的端到端时延及冷/热状态；单个模型请求耗时不等于全链路或生产 P95。

未冻结的类别等价/多专业策略和可接受后备率阈值先集中形成待决项，不由审查者自行定案。本段不授权新的模型调用、环境变更、迁移或发布。

### B4 工单状态机

- 非法转换全部被拒绝并有测试。
- 状态变化有 WorkOrderLog。
- REOPEN 不覆盖旧回执。
- AI 宕机时工单创建仍成功。
- 员工不能把上报异常工单直接完成。
- 业主端不出现“请自行联系第三方”。

### B5 Incident 与公告

- 同楼同类短时多单聚合为 Incident，避免重复派人。
- 公告 AI 草稿不能未审发布。

### B6 知识助手

- 回答含引用；跨小区知识召不中。
- Milvus 不可用时助手失败不影响报修。

### B7 SLA 与通知

- 时限来自配置表。
- Warning / Overdue / Escalation 可测。
- 通知失败可重试并留日志。

### B8 治理

- RBAC + tenant/property 隔离有 IDOR 测试。
- 健康检查、Alembic、结构化日志、request_id、Error Code、上传校验、验证码防刷、幂等、并发接单冲突。

## C. 不验收（V1）

缴费、停车、门禁、访客、商城、ERP、IoT、K8s、微服务、AI 自动联系第三方、GraphRAG、多 Agent。
