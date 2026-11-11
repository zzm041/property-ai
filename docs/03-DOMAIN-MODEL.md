# 03 — 领域模型

本文定义 Production V1 的核心实体。实现时以本文件为准；表名使用 snake_case 复数，核心表主键统一为 `id BIGINT AUTO_INCREMENT`，所有外键使用与目标主键一致的 BIGINT。

## 1. 建模原则

2026-09-24 v2 / 024：已批准独立 `20260924_0011_contact_phone`（父 `20260919_0009`），只增 `work_orders.contact_phone_e164 VARCHAR(16) NULL`，无默认/索引/回填。NULL 表示历史未采集，新创建业务必填。该快照不是账号凭证/关联键，不随换号改变。仅授权迁移 127.0.0.1:3307/property_ai_auth001_test；0010/合并/其他环境仍未批准。待派复用 PENDING_ASSIGNMENT、WorkOrderLog.AUTO_DISPATCH_WAIT、已完成分析及现有调度，不为电话借用 JSON，不新建班组或平行派单表。

- `users.id` 是人的内部永久主键；其他表用 `user_id` BIGINT 外键关联。手机号、微信、密码哈希、身份证都不是外键目标或业务主键。
- BIGINT ID 是否可猜不构成安全边界；资源访问必须执行 RBAC、Tenant/Property Scope、Ownership Policy 与 Anti-IDOR。
- 物理房屋与人分离。House 不存 `owner_name`。
- 住户关系、工单处理、回执都是追加历史，不覆盖。
- 凡跨企业数据必须有 `tenant_id`；小区业务必须有 `property_id`。
- 数据库时间戳统一保存 UTC；自然日业务使用按 Property 时区计算的 `business_date`，默认 `Asia/Shanghai`。
- 软删除仅用于配置类主数据（员工停用、房屋作废）。交易类（工单、关系、审计）不物理覆盖。

## 2. 核心实体列表

### 2.1 组织与房产

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `Tenant` | 物业企业 | name, status, contact |
| `Property` | 小区 | tenant_id, name, address, timezone |
| `Building` | 楼栋 | property_id, code, name |
| `Unit` | 单元 | building_id, code, name |
| `House` | 房屋 | unit_id, room_no, floor, status。**无 owner_name** |

### 2.2 身份

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `User` | 登录主体 | id BIGINT PK, status, display_name, created_at |
| `UserPhone` | 手机绑定 | user_id, phone_e164, verified_at, unique(phone) |
| `UserWechat` | 微信绑定 | user_id, appid, openid, unionid, unique(appid,openid), unique(unionid) where not null |
| `UserPassword` | 密码凭证 | user_id, password_hash, password_algo, updated_at |
| `Role` | 角色字典 | id BIGINT PK, code VARCHAR NOT NULL UNIQUE, name, created_at, updated_at |
| `Permission` | 权限字典 | code, name |
| `RolePermission` | 角色权限 | role_id BIGINT FK -> roles.id, permission_id BIGINT FK -> permissions.id |
| `UserRole` | 用户角色 | user_id BIGINT FK -> users.id, role_id BIGINT FK -> roles.id, scope_type, tenant_id, property_id |
| `SmsChallenge` | 验证码挑战 | phone, purpose, code_hash, expires_at, attempts, client_fingerprint |
| `IdentityRecoveryRequest` | 高风险恢复 | user_id?, real_name, id_number_cipher, provider_ref, liveness_ref, status, reviewer_id |
| `AccountMerge` | 账号合并 | source_user_id, target_user_id, status, operator_id |

说明：身份证号加密存储；人脸只存 Provider 流水与结果。`SmsChallenge` 存哈希，不存明文验证码。

`Role.code` 是稳定且唯一的业务代码，可供 API 展示以及 Service / Seed 查找角色，但不作为关系表的外键。`UserRole` 与 `RolePermission` 均通过 `role_id` 引用 `roles.id`；业务判断通过关联 Role 后读取 `Role.code`。禁止把 `Role.id` 数字硬编码为角色含义；Seed 必须按 `Role.code` 查询 Role、取得其 `id`，再写入 `UserRole.role_id`。

### 2.3 住户关系

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `ResidentBindRequest` | 绑定申请 | user_id, house_id, relation_type, status, auto_check_result, reviewer_id |
| `ResidentHouseRelation` | 正式多对多关系 | user_id, house_id, relation_type, status, valid_from, valid_to, bind_request_id |

`relation_type`：`OWNER` `TENANT` `FAMILY` `AUTHORIZED_OTHER`  
`status`：`PENDING` 只出现在申请表；关系表为 `ACTIVE` `ENDED` `REVOKED`。

退租：将当前行 `status=ENDED`，填写 `valid_to`，需要时插入新关系。禁止 update 覆盖 `relation_type` 历史。

### 2.4 员工

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `Department` | 部门 | tenant_id, property_id?, name |
| `Duty` | 职责 | tenant_id, code, name（如水电维修、保洁） |
| `Skill` | 技能 | tenant_id, code, name |
| `Employee` | 员工档案 | user_id unique, tenant_id, department_id, status, max_capacity |
| `EmployeeProperty` | 员工对小区的有效工作范围历史 | employee_id, property_id, status, valid_from, valid_to |
| `EmployeeDuty` | 员工职责 | employee_id, duty_id |
| `EmployeeSkill` | 员工技能 | employee_id, skill_id |
| `AttendanceRecord` | 签到/签退历史事件 | employee_id, property_id, business_date, check_in_at, check_out_at, source |
| `EmployeeDailyStatus` | 当天运营状态快照 | employee_id, property_id, business_date, on_duty, leave_type, current_load |
| `EmployeeMetric` | 滚动绩效 | employee_id, avg_response_seconds, complete_rate, rating_avg, window |

`Employee` 归属于 Tenant，通过一个或多个 `EmployeeProperty` 服务 Property。关系历史不得覆盖；`valid_to IS NULL` 且状态有效表示当前工作范围。派单读取业务日期当天有效的 `EmployeeProperty` + `EmployeeDailyStatus` + Duty + Skill，不是只读 Employee 静态表。

`check_in_at` / `check_out_at` 保存 UTC Timestamp；`business_date` 是 Property 业务时区下的自然日。签到 Service 必须在同一事务写 `AttendanceRecord` 并更新 `EmployeeDailyStatus`。

### 2.5 工单

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `WorkOrder` | 工单头 | tenant_id, property_id, house_id, reporter_user_id, contact_phone_e164, status, process_reason, category, severity, priority, sla_policy_id, incident_id |
| `WorkOrderContent` | 报修内容 | work_order_id, text, asr_text, extra_notes |
| `WorkOrderMedia` | 图片/语音 | work_order_id, kind, file_id, taken_phase（before/after/on_site） |
| `WorkOrderAssignment` | 派单记录 | work_order_id, employee_id, assigned_by, assigned_at, accept_at, reject_reason, timeout_at |
| `WorkOrderLog` | 状态/过程日志 | work_order_id, from_status, to_status, process_reason, actor, payload_json |
| `WorkOrderProcess` | 一次处理周期 | work_order_id, cycle_no, assignee_id, started_at, ended_at |
| `WorkOrderReceipt` | 回执 | process_id, problem_type, method, materials, fee_amount, fee_flag, resolved, unresolved_reason, follow_up, finished_at |
| `WorkOrderFeedback` | 住户确认/评价 | work_order_id, process_id, confirmed, rating, comment, unresolved_flag |

同一工单可有多个 Process/Receipt。REOPEN 新增 cycle，不删旧 cycle。

### 2.6 事件、公告、知识、SLA、通知、治理

| 实体 | 说明 |
| --- | --- |
| `Incident` | 集中事件：区域、类型、时间窗、主处理人、状态 |
| `IncidentWorkOrder` | Incident 与工单多对多 |
| `Announcement` | 公告主单：草稿/待审/已发布/已下线 |
| `AnnouncementScope` | 范围：tenant / property / building / unit / audience |
| `AnnouncementReview` | 审核记录 |
| `KnowledgeDocument` | 知识文档：tenant_id, property_id, visibility, status |
| `KnowledgeChunk` | 切片元数据；向量在 Milvus，payload 含相同隔离字段 |
| `SlaPolicy` | 按 category+priority(+property) 配置时限 |
| `SlaClock` | 工单时钟：accept/respond/complete due, warning_at, state |
| `Notification` | 业务通知 |
| `NotificationDelivery` | 渠道投递、重试次数、失败原因、下次重试 |
| `FileObject` | 上传文件元数据：hash, mime, size, storage_key, scanner_status |
| `AiCallLog` | 模型调用：prompt_version, model_version, latency, tokens, error |
| `AuditLog` | 安全与管理审计：actor_user_id, tenant_id, property_id, action, resource_type, resource_id, request_id, created_at |
| `IdempotencyRecord` | 幂等键 |
| `TaskIntent`（026批准修订） | `task_intents`：业务同事务的类型化任务意图、有限投递/执行计数及租约；关联既有分析/知识任务/通知记录，不复制业务引擎。0012唯一父版本0011，当前仅批准AUTH001迁移，无历史回填。 |

## 3. ER 关系说明

```text
Tenant 1 ── * Property 1 ── * Building 1 ── * Unit 1 ── * House

User 1 ── 0..1 UserPassword
User 1 ── * UserPhone          （V1 当前有效手机号唯一）
User 1 ── * UserWechat
User 1 ── * UserRole * ── 1 Role
Role 1 ── * RolePermission * ── 1 Permission
User 1 ── 0..1 Employee
User 1 ── * ResidentBindRequest
User 1 ── * ResidentHouseRelation * ── 1 House

Employee 1 ── * EmployeeProperty
Employee 1 ── * EmployeeDuty
Employee 1 ── * EmployeeSkill
Employee 1 ── * AttendanceRecord
Employee 1 ── * EmployeeDailyStatus

House 1 ── * WorkOrder * ── 1 User(reporter)
WorkOrder 1 ── * WorkOrderAssignment * ── 1 Employee
WorkOrder 1 ── * WorkOrderProcess 1 ── 0..1 WorkOrderReceipt
WorkOrder 1 ── * WorkOrderLog
WorkOrder * ── 0..1 Incident

Incident 1 ── * Announcement（草稿来源可选）
KnowledgeDocument ── Milvus chunk by (tenant_id, property_id, visibility)
SlaPolicy 1 ── * WorkOrder
```

### 关系约束

1. `ResidentHouseRelation` 是 User–House 唯一正式关联。不能把业主姓名冗余成房屋主数据。
2. `Employee.user_id` 唯一。停用员工不断开历史工单外键。
3. `EmployeeProperty` 保留工作范围历史；签到与未来派单只接受目标时点有效的关系。
4. 同一 `(employee_id, property_id, business_date)` 只能有一个有效 `EmployeeDailyStatus`。
5. `WorkOrder.property_id` 必须等于 `House` 所属小区。
6. `WorkOrderAssignment.employee_id` 必须能通过有效 `EmployeeProperty` 服务该 `property_id`。
7. `Incident` 与成员工单同 `property_id`。
8. 账号合并：目标 user 保留；当前所有权仅按获批影响清单和冲突检查迁移，历史 actor 不改写；源账户冻结并永久保留合并、号码与审计历史。来源凭证/会话/恢复票据撤销，来源密码不复制。号码复用须经过 30 天隔离、独立新持有人证明和两名不同授权人审核；时间或新短信本身不授予旧身份。所需恢复/审核/合并账本属于待单独批准的新迁移，不借 JSON 绕过。
9. `AuditLog.actor_user_id` 引用 `users.id`，因此 AuditLog Foundation 与 User 同在 Day 1 Task 3；Task 2 不创建 AuditLog。

## 4. 枚举（稳定值）

见状态机文档与下文。新增枚举必须走迁移，禁止前端自由字符串。

| 枚举 | 值 |
| --- | --- |
| `OrganizationScope` | `SYSTEM` `TENANT` `PROPERTY`（不含 SELF） |
| `WorkOrderStatus` | 见 `04-WORKORDER-STATE-MACHINE.md` |
| `ProcessReason` | `NONE` `WAITING_OWNER` `WAITING_PARTS` `INTERNAL_UNRESOLVED` `ESCALATED` `EXTERNAL_COORDINATION` `REASSIGN_REQUIRED` |
| `AttendanceOnDuty` | `ON_DUTY` `OFF_DUTY` `LEAVE` |
| `EmployeePropertyStatus` | `ACTIVE` `ENDED` `REVOKED` |
| `AnnouncementStatus` | `DRAFT` `PENDING_REVIEW` `PUBLISHED` `ARCHIVED` `REJECTED` |
| `Visibility` | `SYSTEM` `TENANT` `PROPERTY` `STAFF_ONLY` `RESIDENT` |

## 5. 索引建议（V1）

- `user_phones.phone_e164` UNIQUE
- `user_wechats(appid, openid)` UNIQUE
- `resident_house_relations(house_id, status, valid_to)`
- `employee_properties(employee_id, property_id, status, valid_to)`
- `employee_daily_statuses(employee_id, property_id, business_date)` UNIQUE
- `employee_daily_statuses(property_id, business_date, on_duty)`
- `work_orders(property_id, status, created_at)`
- `work_orders(incident_id)`
- `audit_logs(tenant_id, created_at)`
- `idempotency_records(user_id, key)` UNIQUE（D1：键的作用域是提交人；全局唯一会让一个用户占用键并挡住别人建单）

## 6. 明确不建的表（V1）

缴费账单、车位、门禁卡、访客预约、商品 SKU、采购单、IoT 设备影子、第三方服务商结算。
