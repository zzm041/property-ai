# 06 — API 契约（V1 初稿）

**Base URL：** `/api/v1`  
**协议：** HTTPS（生产）/ JSON  
**认证：** Bearer Access Token（登录下发）。刷新令牌与访问令牌分离。  
**通用头：** `Authorization`、`X-Request-Id`（可选，服务端没有则生成）、`X-Property-Id`（小区上下文）、`Idempotency-Key`（创建类）。

本文是契约初稿，不是代码。实现前如需改路径，先改本文并经负责人确认。

2026-09-24 v2 / 024：`POST /work-orders` 新增必填 `contact_phone`，复用 E.164 规范化（中国手机简写、带国际区号固定电话）。缺失/非法422，规范化电话参与幂等哈希，同 key 不同电话409不覆盖。`GET /me/contact-phone` 只读本人 ACTIVE 号码供确认默认值。`GET /work-orders/{id}/contact` 返回 `{collected, masked_phone, phone:null}`；`?reveal=true` 对本人/当前有效负责员工/对应组织授权管理者显式返回完整号码并审计，SYSTEM-only 无明文权限，无关用户/跨域403。电话响应 `Cache-Control:no-store`。列表/通用 WorkOrderData/display 不返回完整号码，历史 NULL 不回填；联系操作不把电话放 URL/模型/公开证据。

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
| `STATS` | 运营统计 |
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
| POST | `/system/organizations` | SYSTEM_ADMIN 建立租户、首个小区并授予已注册有效账号 TENANT_ADMIN；三者同事务 |
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

`POST /system/organizations` 要求 `Idempotency-Key`；同键同请求返回原租户、小区和首位管理员，不重复建立授权或审计，同键异体请求返回冲突。首位管理员以当前 ACTIVE 手机凭证定位，手机号必须先经正常认证流程注册；客户端不能提交 user_id、角色或“已认证”标记。服务端重新加载当前 SYSTEM_ADMIN 权限，在一个事务内建立 Tenant、Property、TENANT_ADMIN grant、幂等结果和审计。名称/授权冲突时整体回滚，不留下半成品组织。该接口不读取其他住户号码，也不改变账号手机号。

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
| POST | `/staff/work-orders/{id}/feedback` | 追加过程反馈，可省略或原样保留 process_reason；变更原因须走异常上报/主管决定，避免绕过通知及决策 |
| POST | `/staff/work-orders/{id}/receipts` | 正式回执 |
| POST | `/staff/work-orders/{id}/receipts/{receipt_id}/media` | 待住户确认时补传当前周期回执缺失的处理前/后媒体 |
| POST | `/staff/work-orders/{id}/escalate` | 无法解决上报 |
| POST | `/supervisor/work-orders/{id}/assign` | 人工派单/改派 |
| POST | `/supervisor/work-orders/{id}/decide` | 异常后续方案 |
| GET | `/supervisor/work-orders` | 小区队列 |

小区队列保留默认 `view=dispatch`；`view=unstarted` 仅列出当前冻结 FSM 允许 CANCEL 的未开工状态（PENDING_ANALYSIS/PENDING_ASSIGNMENT/ASSIGNED/ACCEPTED），沿用 `property_id/tenant_id/after_id/limit` 和服务端 Scope 校验。撤销仍复用原 `/work-orders/{id}/cancel` 与 `version`：住户只能撤销本人未派单工单，其他报修人只能由该小区具备管理及撤销权限的人员操作；不开放 PROCESSING/WAIT_CONFIRM 撤销。结束派单、释放或转移共同事件容量、停止 SLA、日志/审计及通知属于同一业务事务。

创建工单：`Idempotency-Key` 必填（客户端生成）。重复键返回同一工单，不建第二条。键的作用域是提交人：`(user_id, key)` 唯一，两个用户各自使用相同字符串互不影响；同一用户同键异体请求仍返回 409。

创建成功响应即含 `id` 与 `status=PENDING_ANALYSIS`，不等待 AI。

2026-11-01 字段隔离修订：`GET /work-orders/{id}` 接受 `audience=resident|property`，默认 resident；参数只请求视图，不授予权限。property 必须额外具备该工单的合法物业读取资格，报修人本人或双角色账号不能仅凭所有权取得该视图。员工/主管页面显式请求 property。住户保留进度、回执、费用、未解决原因和后续安排；不返回内部派单理由、操作人标识、内部分析日志及混合 extra_notes。AI_TASK_FAILED/AI_TASK_ATTEMPT 只输出必要生命周期状态，不输出诊断正文，以支持失败接管提示。

property 详情中的 assignment 若来自系统自动派单，则 `assigned_by_user_id=null`；人工派单仍返回实际操作者。resident 不返回 assigned_by_user_id，仅保留安全自动派单标记；自动来源须由持久化信息及审计核对，不能由客户端伪造。

resident 的补充文字只从明确住户 APPEND 来源恢复；历史混合备注不猜测归属，不删除原文，property 仍可查阅。公开日志 reason 仅限本人住户 APPEND/REOPEN/CANCEL，主管本人报修的 DECIDE/ESCALATE 不因此公开。

`GET /files/{id}/content` 同样支持上述 audience；关联工单的历史文件不再仅凭上传者身份放行。resident 只读 REPORT 或明确绑定有效回执的 BEFORE/AFTER；ON_SITE 为内部过程资料。新增文件关联也重验来源可见性，不能把不可读历史文件或内部过程文件通过重新关联变成住户附件。未关联任何工单的本人上传保留预览。拒绝沿用 FILE.NOT_FOUND，不读取存储；原始媒体和历史关联不清理。

回执媒体补传仅允许当前负责员工在 `WAIT_CONFIRM` 状态使用，并须携带当前工单 `version`。服务端必须重新锁定当前派单关系，校验 `work-order:resolve` 权限，并确认 `receipt_id` 属于当前处理周期；客户端传入的周期、回执、上传者或组织归属均不得直接信任。补传只新增明确的 `BEFORE` / `AFTER` 关联媒体，不创建新处理周期或回执；媒体关联、工单版本、`WorkOrderLog` 和 `AuditLog` 必须在同一事务内完成。旧周期和历史回执不得改写。

工单详情可附加只读 `display`：`property_timezone`、`timestamp_labels`（以原始 UTC 时间字符串为键，给出该小区时区及实际偏移的展示文字）、`assignee_names`（仅该已授权工单的派单/处理员工当前展示名）。原始 ID、UTC 时间、状态和历史关联不替换；展示名不是历史姓名快照，不返回手机号或凭证。缺少姓名时页面标明员工编号；接口无合法时区时明确回退 UTC。必须先通过原工单读取授权，不提供人员枚举接口，也不改变状态机和操作权限。

2026-11-06 真机验收展示修订（用户确认）：小程序各页面的时间统一从原始时间值转换为北京时间，显示 `YYYY-MM-DD HH:mm`，不展示秒、小数或时区后缀；不直接渲染上述接口时间标签。数据库 UTC、并发校验原始时间和小区业务日期计算保持不变。员工“无法处理”通过回执后的入口进入独立上报页，沿用现有 escalate 接口、状态与版本校验。

2026-11-02 回执内部实际处理记录：`POST /staff/work-orders/{id}/receipts` 可选 `handling_mode=SELF|ASSISTED|EXTERNAL`、`handling_note`（最多255字符）。省略表示未记录，不推定由负责人处理；协作/外部支援必须填写参与人员或单位及分工。它是提交者自报，不是资格认定或质量评价；不填写私人联系方式。字段仅在本周期首次提交回执时接受，既有权限、版本和一周期一回执约束不变。

同事务 `WorkOrderLog.payload_json.receipt_handling_v1` 保存 `receipt_id/process_id/mode/note/reported_participant_employee_id`；仅 SELF/ASSISTED 关联提交者的员工ID，代理提交不能错误关联原负责人。日志 actor 是记录人，Process.assignee_id 仍是负责人。物业历史显示相应内部 reason；住户白名单不返回该说明或参与信息，仍保留结果、费用、未解决原因和跟进。历史不回填，无数据迁移；现有统计不因此升级为真实参与人质量统计。

## 7. Incident / 公告 / 知识 / SLA / 通知 / 文件

| 方法 | 路径 |
| --- | --- |
| GET | `/supervisor/incidents` |
| GET | `/supervisor/incidents/{id}` |
| POST | `/supervisor/incidents/{id}/summary` | 触发 AI 摘要 |
| POST | `/supervisor/incidents/{id}/status` | 具备目标范围 `incident:manage` 的人员提交 `status`、必填 `reason` 与 `expected_updated_at`；冲突 409；不批量改变成员工单 |
| POST | `/admin/announcements` | 草稿，可带 ai_draft |
| GET | `/admin/announcements` | 当前管理范围公告及定向、审核、审计历史 |
| GET | `/admin/announcements/{id}` | 管理详情及历史 |
| PATCH | `/admin/announcements/{id}` | 编辑草稿/被驳回公告及定向范围 |
| POST | `/admin/announcements/{id}/submit-review` |
| POST | `/admin/announcements/{id}/publish` | 人工批准或带原因驳回；未审核 409 |
| GET | `/announcements` | 住户/员工仅获取已发布且命中房屋、任职及人群范围的公告 |

2026-11-01 公告读取增加 `audience=resident|property`，默认 resident。住户用途要求有效房屋关系与目标范围读取权限，仅返回对应住户人群公告；双角色不会自动带出内部公告。物业用途须显式请求，并校验同一授权内的管理角色/权限/范围，或本租户有效员工服务关系及本范围员工读取权限。旧员工调用同步增加 property 参数；参数本身不授予权限。列表/详情切换用途、小区或重新进入时清空历史结果并拒收旧异步响应。个人历史通知保留政策不在本修订中改变。

`GET /supervisor/work-orders/{id}/diagnosis?property_id={id}&version={version}`：主管只读处理诊断。要求当前身份具备工单目标范围的 `work_order:dispatch`，校验声明小区和选中版本。返回读取时刻、带来源ID/时间的固定事实及建议；最后派单等待记录是历史证据，不代表当前资格，`current_eligibility_checked=false`。不返回原始日志/正文/员工画像，不调用模型或更改工单。页面上下文改变或隐藏时清空结果，旧请求不得覆盖新选择。
| POST | `/assistant/query` | RAG 问答，返回 answer + citations |
| GET | `/admin/knowledge-documents` | List authorized tenant/property knowledge documents with version and ingestion status |
| GET | `/admin/knowledge-documents/{id}` | Read one authorized knowledge document and its version history |
| POST | `/admin/knowledge-documents` | Create tenant/property knowledge and enqueue version 1 ingestion |
| POST | `/admin/knowledge-documents/{id}/versions` | Add a source-only version and enqueue ingestion |
| POST | `/admin/knowledge-documents/{id}/archive` | Archive a document; archived content is excluded from retrieval |
| GET/PUT | `/admin/sla-policies` |
| GET | `/admin/operations-overview` | Property/Tenant 管理员按获授权范围读取当前状态快照；不返回业务正文、人员身份或部署配置 |
| GET | `/notifications` |
| POST | `/notifications/{id}/read` |
| POST | `/files` | 注册文件元数据，取得 `file_id` |
| POST | `/files/{id}/content` | 上传文件内容；校验声明大小、扩展名、MIME 魔数并记录内容哈希；相同字节重试幂等，不允许替换已上传内容 |
| GET | `/files/{id}/content` | 按 audience、当前工单权限与媒体可见性鉴权；仅未关联工单的本人上传保留上传者预览例外 |
| GET | `/healthz` | 进程存活 |
| GET | `/readyz` | 报告 MySQL、Redis、RabbitMQ；非关键依赖可显示 degraded |

`/assistant/query` 的请求 `audience` 为 `resident`（默认）或 `property`。住户用途仅召回 RESIDENT 文档；双角色也不会自动带出内部资料。物业内部查询必须显式传 `property`，服务端按当前角色和有效关系鉴权；旧内部调用需同步加上该字段。小程序默认住户视图，物业账号可显式选择内部用途，切换用途/小区或重新进入时清空结果并丢弃旧异步响应。用途参数本身不能授予权限。

选定小区时，住户角色需匹配目标 knowledge:read 且有该租户/小区的有效房屋关系；普通 STAFF 需匹配角色权限、ACTIVE Employee、同租户与有效服务关系。仅关系或仅残留角色均不能放行。现有 SUPERVISOR/PROPERTY_ADMIN 明确管理授权保留，不要求管理账号额外建立普通员工档案；TENANT_ADMIN/SYSTEM_ADMIN 仍需匹配目标和读取权限。不指定小区的租户级查询仅允许该租户管理员或系统管理员。角色与关系在以下各读取阶段均重验。

`/assistant/query` 在初始鉴权、召回后、生成前和返回有效答案前分别使用独立短读取事务，重新加载账号权限与范围策略；embedding、向量检索、重排与生成不持有查询 Session 事务。调用入口拒绝调用者已开启的事务，不代其提交或回滚。

生成使用不可变证据快照。生成前和返回前复核全部选中片段（含未被最终引用的上下文）的有效状态、当前版本、租户/小区/可见性及正文、标题、定位；发生变更则返回固定 `knowledge_unavailable` 和空引用，权限撤回沿用鉴权错误，不展示旧答案。引用从复核过的快照构造。该保证止于最后一次读取边界，不承诺与网络发送原子化。真实 MySQL 隔离与连接池行为见批次 D 待验证记录；关系/用途边界见批次 E，不以模拟测试替代真实环境验收。

## 8. 上传校验

- 大小上限环境变量 `UPLOAD_MAX_MB`
- MIME allowlist + 魔数校验
- `file_id` 关联工单前必须属于当前操作人，并与工单 tenant/property 一致；不得通过猜测 ID 引用他人附件
- 图片/音频病毒扫描位（V1 可先记 `scanner_status=skipped_dev`，生产接 Provider）
- 不接受可执行文件后缀

## 9. 小程序对应关系

同一微信小程序调用上述 API。角色只影响可见路径；越权一律 403 `IAM.FORBIDDEN`，不返回其他租户数据。

## 10. 可靠任务接管（026，负责人已批准的0012范围）

业务提交与 `task_intents` 同事务。API成功不等于异步完成，PUBLISHED/Celery task_success也不等于业务成功。Worker复用原分析、派单、入库和通知服务；租约、重试预算与幂等由持久意图管理。启用前必须应用获批准的0012并配置有界投递进程；`DURABLE_TASKS_ENABLED` 默认关闭，不自动迁移或回放历史数据。

- `GET /admin/task-intents/{id}`：读取准确任务的状态、资源ID/版本、generation、发布/执行计数和稳定错误；不返回输入正文、电话、凭据或执行租约token。
- `POST /admin/task-intents/{id}/retry`：只有明确FAILED任务可接管，请求仅有 `reason_code=DEPENDENCY_RECOVERED|OPERATOR_VERIFIED`，禁止任意task名/Scope/载荷。资源必须仍可处理；取消、结束、新业务版本或新任务优先。保留旧generation和失败，持锁建立唯一新generation并写TASK_INTENT_RETRIED审计；重复请求不再建立第二条或重置旧计数。该入口是异常接管，不参与普通逐单派单。
- 按原资源及最新认证主体核对权限：分析使用 `ai_analysis:review`，知识使用 `knowledge:manage`，通知运维使用对应组织的 `property:manage`。不得混拼不同角色/租户的权限与范围。不可见ID统一404 `TASK.NOT_FOUND` 防止枚举；状态冲突409 `TASK.CONFLICT`，未启用409 `TASK.NOT_ENABLED`，非法输入422。
- 自动投递最多5次（1/2/4/8秒退避），分析/入库执行最多3次、通知4次，不能叠乘Celery重试。执行租约120秒、心跳20秒、单次硬期限900秒；投递租约15秒、确认未领取期限120秒。只有明确操作员接管才创建下一generation；正常重投不增加generation。
- 系统允许消息重复，业务派单和员工占用不得重复。外部通知在供应商未提供幂等保障时不承诺exactly-once。隔离演练只处理具名新intent清单，不扫描历史工单或消费历史DLQ。
# 2026-11-02 增量：物业内部负责工单履历

`GET /api/v1/admin/employees/{employee_id}/work-history?tenant_id=&property_id=&before_id=0&limit=20`

要求当前身份对目标小区同时有 `employee:read` 与 `work_order:dispatch`，每次重新核权；员工须属该企业且在该小区有任职历史。`X-Property-Id` 若提供须匹配。游标按处理轮次ID倒序，limit为1—20；返回 `employee_id/tenant_id/property_id/items/next_cursor`。

每项只含 process_id、work_order_id、cycle_no、当前category/current_order_status、started_at/ended_at、receipt_id/receipt_finished_at、reported_resolved、handling_mode、reported_participant_employee_id、declaration_log_id，以及最新同轮反馈 `latest_feedback(id,recorded_at,confirmed,rating,unresolved)`。无回执/声明/反馈保持null；不推断已解决或由负责人亲自完成。声明来源为回执日志，SELF/ASSISTED提交者可能不是负责人，EXTERNAL不归功于负责人。最新一条反馈不是完整统计。分类/状态是当前值，不冒称历史快照。

不返回住户身份、电话、地址、反馈自由文本或日志原文；不改变质量分母、派单分数或容量政策，不承诺覆盖该员工所有协作参与。

