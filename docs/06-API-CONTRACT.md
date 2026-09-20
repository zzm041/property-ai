# 06 — API 契约（V1 初稿）

**Base URL：** `/api/v1`  
**协议：** HTTPS（生产）/ JSON  
**认证：** Bearer Access Token（登录下发）。刷新令牌与访问令牌分离。  
**通用头：** `Authorization`、`X-Request-Id`（可选，服务端没有则生成）、`X-Property-Id`（小区上下文）、`Idempotency-Key`（创建类）。

本文是契约初稿，不是代码。实现前如需改路径，先改本文并经负责人确认。

资源 ID 是正 BIGINT。ID 可枚举性不能作为安全边界；路径、Header 或 Body 中的 `tenant_id` / `property_id` 仅用于定位上下文，后端必须依据认证用户、组织 Scope、目标资源与数据库关系重新授权并防止 IDOR。

## 1. 统一响应

成功：

```json
{
  "request_id": "01J...",
  "data": {}
}
```

失败：

```json
{
  "request_id": "01J...",
  "error": {
    "code": "AUTH.INVALID_CODE",
    "message": "验证码不正确或已过期",
    "details": {}
  }
}
```

列表：`data.items` + `data.next_cursor`（游标分页，避免深 OFFSET）。

## 2. Error Code 前缀

| 前缀 | 域 |
| --- | --- |
| `AUTH` | 登录、验证码、令牌 |
| `IAM` | 权限、角色、范围 |
| `IDN` | 身份绑定、合并、恢复 |
| `RES` | 房屋、住户关系 |
| `EMP` | 员工、签到 |
| `WO` | 工单、派单、回执 |
| `INC` | 集中事件 |
| `ANN` | 公告 |
| `KB` | 知识助手 |
| `SLA` | SLA |
| `NTF` | 通知 |
| `FILE` | 上传 |
| `SYS` | 健康、限流、校验 |

4xx 业务可重试与否由 code 文档标明。5xx 不暴露内部栈。

## 3. 认证

| 方法 | 路径 | 说明 | 幂等 |
| --- | --- | --- | --- |
| POST | `/auth/wechat/login` | `code` → 登录或需绑定手机 | 否，微信 code 一次性 |
| POST | `/auth/sms/send` | 发验证码，purpose=login\|reset\|change_phone | 同一手机+purpose 限流 |
| POST | `/auth/sms/login` | 手机号+验证码 | 否 |
| POST | `/auth/password/login` | 手机号+密码 | 否 |
| POST | `/auth/token/refresh` | 刷新 | 是（旋转 refresh） |
| POST | `/auth/logout` | 作废 refresh | 是 |
| POST | `/auth/password/change` | 登录后改密 | 否 |
| POST | `/auth/password/reset` | 验证码重置 | 否 |
| POST | `/auth/phone/change` | 换绑手机 | 否 |
| POST | `/auth/wechat/rebind` | 换绑微信 | 否 |
| POST | `/auth/account/merge/preview` | 预览合并影响 | 是 |
| POST | `/auth/account/merge/confirm` | 执行合并 | Idempotency-Key |
| POST | `/auth/recovery` | 提交实名恢复 | Idempotency-Key |
| GET | `/me` | 当前用户、角色、房屋摘要 | 是 |

### 3.1 短信发送失败

- `AUTH.SMS_RATE_LIMITED` 频控
- `AUTH.SMS_PROVIDER_DOWN` 供应商失败，可稍后重试
- 不泄露“该手机是否已注册”的差异时序（登录/注册同一入口）

## 4. 房产与住户

| 方法 | 路径 | 角色 |
| --- | --- | --- |
| GET | `/properties` | 登录用户可见范围 |
| GET | `/properties/{id}/buildings` | 住户申请绑定或管理员 |
| GET | `/buildings/{id}/units` | 同上 |
| GET | `/units/{id}/houses` | 同上 |
| POST | `/resident-bind-requests` | 住户申请 |
| GET | `/resident-bind-requests/mine` | 住户 |
| GET | `/admin/resident-bind-requests` | 物业审核队列 |
| POST | `/admin/resident-bind-requests/{id}/approve` | 管理员 |
| POST | `/admin/resident-bind-requests/{id}/reject` | 管理员 |
| GET | `/me/houses` | 有效关系 |
| POST | `/admin/houses` | 维护档案 |
| PATCH | `/admin/houses/{id}` | 维护档案 |

批准申请必须创建 `ResidentHouseRelation`，而不是改 House 姓名字段。

## 5. 员工

| 方法 | 路径 |
| --- | --- |
| POST | `/staff/attendance/checkin` |
| POST | `/staff/attendance/checkout` |
| PATCH | `/staff/attendance/status` |
| GET | `/staff/attendance/today` |
| GET | `/supervisor/staff/daily` |
| GET | `/admin/employees` |
| POST | `/admin/employees` |
| PATCH | `/admin/employees/{id}` |
| GET | `/admin/employees/{id}` |
| GET | `/admin/employee-catalog` |
| POST | `/admin/duties` |
| POST | `/admin/skills` |
| POST | `/admin/employees/{id}/property-assignments` |
| POST | `/admin/employees/{id}/property-assignments/{assignment_id}/end` |
| POST | `/admin/employees/{id}/invites` |

签到请求携带目标 `property_id`，服务端须校验 Employee 在该 Property 当前存在有效 `EmployeeProperty`，不能信任客户端声明。成功响应至少包含：

- `business_date`：Property 业务时区下的自然日，默认 `Asia/Shanghai`
- `check_in_at` / `check_out_at`：UTC Timestamp
- 当天 `EmployeeDailyStatus`

签到失败（不在有效服务范围、篡改 property_id、重复签到）返回 `EMP.CHECKIN_DENIED`。签到写入 `AttendanceRecord` 与更新 `EmployeeDailyStatus` 必须处于同一 Service 事务。

职责、技能目录属于 Tenant，创建和查询时仍须以目标 Property 做 RBAC 范围校验。员工更新使用 `add_duty_ids` / `add_skill_ids` 追加当前能力关系；不得以修改接口删除既有历史证据。

`GET /admin/employees` 必须携带 `tenant_id`、`property_id`，默认只返回该小区当前有效任职的员工；`include_historical=true` 时也返回曾在该小区任职、可重新建立任职的员工。接口按目标小区执行 `employee:read` 权限校验，支持 `after_id` / `limit` 游标分页。目录响应可包含该 Tenant 全局部门及目标小区专属部门；其他小区专属部门不得显示。

## 6. 工单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/work-orders` | 创建。媒体先上传拿 file_id。AI 异步 |
| GET | `/work-orders/{id}` | 详情+时间线 |
| POST | `/work-orders/{id}/append` | 补充说明/媒体 |
| POST | `/work-orders/{id}/cancel` | 撤销 |
| POST | `/work-orders/{id}/confirm` | 确认解决 |
| POST | `/work-orders/{id}/reopen` | 未解决 |
| POST | `/work-orders/{id}/rate` | 评价 |
| GET | `/staff/work-orders` | 我的任务 |
| POST | `/staff/work-orders/{id}/accept` | 接单 |
| POST | `/staff/work-orders/{id}/reject` | 拒单+原因 |
| POST | `/staff/work-orders/{id}/start` | 开始处理 |
| POST | `/staff/work-orders/{id}/feedback` | 过程反馈 |
| POST | `/staff/work-orders/{id}/receipts` | 正式回执 |
| POST | `/staff/work-orders/{id}/escalate` | 无法解决上报 |
| POST | `/supervisor/work-orders/{id}/assign` | 人工派单/改派 |
| POST | `/supervisor/work-orders/{id}/decide` | 异常后续方案 |
| GET | `/supervisor/work-orders` | 小区队列 |

创建工单：`Idempotency-Key` 必填（客户端生成）。重复键返回同一工单，不建第二条。

创建成功响应即含 `id` 与 `status=PENDING_ANALYSIS`，不等待 AI。

工单详情中的 assignment 若来自系统自动派单，则 `assigned_by_user_id=null`；人工派单仍返回实际操作者。自动来源必须由服务端持久化的 `assigned_source=AUTO` 与审计/状态日志共同判定，不能由客户端伪造。

## 7. Incident / 公告 / 知识 / SLA / 通知 / 文件

| 方法 | 路径 |
| --- | --- |
| GET | `/supervisor/incidents` |
| GET | `/supervisor/incidents/{id}` |
| POST | `/supervisor/incidents/{id}/summary` | 触发 AI 摘要 |
| POST | `/admin/announcements` | 草稿，可带 ai_draft |
| GET | `/admin/announcements` | 当前管理范围公告及定向、审核、审计历史 |
| GET | `/admin/announcements/{id}` | 管理详情及历史 |
| PATCH | `/admin/announcements/{id}` | 编辑草稿/被驳回公告及定向范围 |
| POST | `/admin/announcements/{id}/submit-review` |
| POST | `/admin/announcements/{id}/publish` | 人工批准或带原因驳回；未审核 409 |
| GET | `/announcements` | 住户/员工仅获取已发布且命中房屋、任职及人群范围的公告 |
| POST | `/assistant/query` | RAG 问答，返回 answer + citations |
| GET/PUT | `/admin/sla-policies` |
| GET | `/notifications` |
| POST | `/notifications/{id}/read` |
| POST | `/files` | 注册文件元数据，取得 `file_id` |
| POST | `/files/{id}/content` | 上传文件内容；校验声明大小、扩展名、MIME 魔数并记录内容哈希；相同字节重试幂等，不允许替换已上传内容 |
| GET | `/files/{id}/content` | 鉴权读取已上传内容；仅上传者或可读关联工单的主体可访问 |
| GET | `/healthz` | 进程存活 |
| GET | `/readyz` | 报告 MySQL、Redis、RabbitMQ；非关键依赖可显示 degraded |

## 8. 上传校验

- 大小上限环境变量 `UPLOAD_MAX_MB`
- MIME allowlist + 魔数校验
- `file_id` 关联工单前必须属于当前操作人，并与工单 tenant/property 一致；不得通过猜测 ID 引用他人附件
- 图片/音频病毒扫描位（V1 可先记 `scanner_status=skipped_dev`，生产接 Provider）
- 不接受可执行文件后缀

## 9. 小程序对应关系

同一微信小程序调用上述 API。角色只影响可见路径；越权一律 403 `IAM.FORBIDDEN`，不返回其他租户数据。
