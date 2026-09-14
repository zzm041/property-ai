# 03 — 领域模型

本文定义 Production V1 的核心实体。实现时以本文件为准；表名使用 snake_case 复数，主键除特别说明外为 UUID `CHAR(36)`。

## 1. 建模原则

- `user_id` 是人的主键。手机号、微信、密码哈希都不是外键目标。
- 物理房屋与人分离。House 不存 `owner_name`。
- 住户关系、工单处理、回执都是追加历史，不覆盖。
- 凡跨企业数据必须有 `tenant_id`；小区业务必须有 `property_id`。
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
| `User` | 登录主体 | user_id PK, status, display_name, created_at |
| `UserPhone` | 手机绑定 | user_id, phone_e164, verified_at, unique(phone) |
| `UserWechat` | 微信绑定 | user_id, appid, openid, unionid, unique(appid,openid), unique(unionid) where not null |
| `UserPassword` | 密码凭证 | user_id, password_hash, password_algo, updated_at |
| `Role` | 角色字典 | code, name |
| `Permission` | 权限字典 | code, name |
| `RolePermission` | 角色权限 | role_id, permission_id |
| `UserRole` | 用户角色 | user_id, role_code, scope_type, tenant_id, property_id |
| `SmsChallenge` | 验证码挑战 | phone, purpose, code_hash, expires_at, attempts, client_fingerprint |
| `IdentityRecoveryRequest` | 高风险恢复 | user_id?, real_name, id_number_cipher, provider_ref, liveness_ref, status, reviewer_id |
| `AccountMerge` | 账号合并 | source_user_id, target_user_id, status, operator_id |

说明：身份证号加密存储；人脸只存 Provider 流水与结果。`SmsChallenge` 存哈希，不存明文验证码。

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
| `EmployeeProperty` | 员工可服务小区 | employee_id, property_id |
| `EmployeeDuty` | 员工职责 | employee_id, duty_id |
| `EmployeeSkill` | 员工技能 | employee_id, skill_id |
| `AttendanceRecord` | 签到/签退 | employee_id, property_id, work_date, checkin_at, checkout_at, source |
| `EmployeeDailyStatus` | 当天状态快照 | employee_id, property_id, work_date, on_duty, leave_type, current_load |
| `EmployeeMetric` | 滚动绩效 | employee_id, avg_response_seconds, complete_rate, rating_avg, window |

派单读取 **当天** `AttendanceRecord` + `EmployeeDailyStatus`，不是只读 Employee 静态表。

### 2.5 工单

| 实体 | 说明 | 关键字段 |
| --- | --- | --- |
| `WorkOrder` | 工单头 | tenant_id, property_id, house_id, reporter_user_id, status, process_reason, category, severity, priority, sla_policy_id, incident_id |
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
| `AuditLog` | 安全与管理审计 |
| `IdempotencyRecord` | 幂等键 |
| `RequestOutbox` 可选 | V1 可用通知表代替完整 outbox |

## 3. ER 关系说明

```text
Tenant 1 ── * Property 1 ── * Building 1 ── * Unit 1 ── * House

User 1 ── 0..1 UserPassword
User 1 ── * UserPhone          （V1 当前有效手机号唯一）
User 1 ── * UserWechat
User 1 ── * UserRole
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
3. `WorkOrder.property_id` 必须等于 `House` 所属小区。
4. `WorkOrderAssignment.employee_id` 必须能服务该 `property_id`。
5. `Incident` 与成员工单同 `property_id`。
6. 账号合并：身份绑定迁到 target user；工单 reporter / 关系 user_id 一并迁移；源账户冻结并留合并审计。

## 4. 枚举（稳定值）

见状态机文档与下文。新增枚举必须走迁移，禁止前端自由字符串。

| 枚举 | 值 |
| --- | --- |
| `WorkOrderStatus` | 见 `04-WORKORDER-STATE-MACHINE.md` |
| `ProcessReason` | `NONE` `WAITING_OWNER` `WAITING_PARTS` `INTERNAL_UNRESOLVED` `ESCALATED` `EXTERNAL_COORDINATION` `REASSIGN_REQUIRED` |
| `AttendanceOnDuty` | `ON_DUTY` `OFF_DUTY` `LEAVE` |
| `AnnouncementStatus` | `DRAFT` `PENDING_REVIEW` `PUBLISHED` `ARCHIVED` `REJECTED` |
| `Visibility` | `SYSTEM` `TENANT` `PROPERTY` `STAFF_ONLY` `RESIDENT` |

## 5. 索引建议（V1）

- `user_phone.phone_e164` UNIQUE
- `user_wechat(appid, openid)` UNIQUE
- `resident_house_relation(house_id, status, valid_to)`
- `work_order(property_id, status, created_at)`
- `work_order(incident_id)`
- `employee_daily_status(property_id, work_date, on_duty)`
- `audit_log(tenant_id, created_at)`
- `idempotency(key)` UNIQUE

## 6. 明确不建的表（V1）

缴费账单、车位、门禁卡、访客预约、商品 SKU、采购单、IoT 设备影子、第三方服务商结算。
