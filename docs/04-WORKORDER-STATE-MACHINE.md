# 04 — 工单状态机

## 1. 目标

主状态保持稳定，避免状态爆炸。PROCESSING 内的等待/升级用 `process_reason` 表达。  
每一次状态或 `process_reason` 变化都必须写入 `WorkOrderLog`，并同步写一条可追溯的 `AuditLog`（人工高权限操作时）。

## 2. 主状态

| 状态 | 含义 | 谁驱动 |
| --- | --- | --- |
| `PENDING_ANALYSIS` | 已创建，等待 AI 或人工分析 | 系统 / 人工分析员 |
| `PENDING_ASSIGNMENT` | 已有类型/职责，等待派单 | 派单引擎 / 主管 |
| `ASSIGNED` | 已派给具体员工，未接单 | 系统 / 主管 |
| `ACCEPTED` | 员工已接单，未开始现场处理 | 员工 |
| `PROCESSING` | 处理中（含等待配件、升级等子原因） | 员工 / 主管 |
| `WAIT_CONFIRM` | 员工声明处理完成，等待住户确认 | 员工提交回执后 |
| `COMPLETED` | 住户确认已解决，或规则确认 | 住户 / 超时确认策略 |
| `CANCELLED` | 撤销或未开工关闭 | 住户 / 管理员 |
| `CLOSED` | 终态归档（完成后归档，或取消后归档） | 系统定时 / 管理员 |

`REOPEN` **不是主状态**，是从 `WAIT_CONFIRM`（或误标完成）回到处理流的事件。

## 3. process_reason（仅当 status = PROCESSING 时有业务含义）

| 值 | 含义 | 员工可否单独结束工单 |
| --- | --- | --- |
| `NONE` | 正常处理 | 可以提交回执进入 WAIT_CONFIRM |
| `WAITING_OWNER` | 等待业主配合 | 否，保持 PROCESSING |
| `WAITING_PARTS` | 等待配件 | 否 |
| `INTERNAL_UNRESOLVED` | 内部无法解决，已上报 | 否，须主管决策 |
| `ESCALATED` | 已升级主管 | 否 |
| `EXTERNAL_COORDINATION` | 物业正在内部协调外部专业服务 | 否，业主端只显示物业继续处理 |
| `REASSIGN_REQUIRED` | 需要重新派单 | 否，回到派单 |

其他状态时 `process_reason` 必须为 `NONE`。

## 4. 合法转换

```text
                    创建
                      │
                      ▼
              PENDING_ANALYSIS
                 │            │AI/人工完成     │用户撤销（未派单）
                 ▼            │                ▼
         PENDING_ASSIGNMENT   │            CANCELLED ──► CLOSED
                 │            │
                 │派单成功    │无候选/失败保持待派或人工
                 ▼
              ASSIGNED
           │接单     │拒单/超时
           ▼         └────────► PENDING_ASSIGNMENT（新 Assignment 行）
        ACCEPTED
           │开始处理
           ▼
        PROCESSING ◄────────────────────────────┐
           │                                      │
           │提交已解决回执                        │REOPEN（住户反馈未解决）
           ▼                                      │
        WAIT_CONFIRM ─────────────────────────────┘
           │住户确认 / 确认超时策略
           ▼
        COMPLETED ──► CLOSED
```

主管在 `PROCESSING` 中可把 `process_reason` 改为上表值，或触发：

- `REASSIGN_REQUIRED` → `PENDING_ASSIGNMENT`
- 维持等待类原因，工单仍为 `PROCESSING`

## 5. 转换规则（必须实现为领域函数，禁止散落 if）

| 从 | 到 | 条件 | 失败 |
| --- | --- | --- | --- |
| 创建 | PENDING_ANALYSIS | 内容至少有文本、图片或语音之一；房屋关系有效或管理员代报 | 校验失败不建单 |
| PENDING_ANALYSIS | PENDING_ASSIGNMENT | 结构化分析结果有效，或人工补全 | AI 失败则保持本状态并入人工队列，**工单已存在** |
| PENDING_ANALYSIS | CANCELLED | 报修人撤销 | 已进入 ASSIGNED 之后的撤销走管理员 |
| PENDING_ASSIGNMENT | ASSIGNED | 自动或人工指定在岗员工 | 无候选：保持待派并告警，不假派 |
| ASSIGNED | ACCEPTED | 被派员工接单 | 非被派员工 403 |
| ASSIGNED | PENDING_ASSIGNMENT | 拒单（原因必填）或接单超时 | 拒单无原因 400 |
| ACCEPTED | PROCESSING | 开始处理 | — |
| ACCEPTED | PENDING_ASSIGNMENT | 主管改派 | 写日志 |
| PROCESSING | PROCESSING | 仅改 process_reason | 员工不能把 INTERNAL_UNRESOLVED 直接改 COMPLETED |
| PROCESSING | WAIT_CONFIRM | 提交“已处理”回执且 resolved=true | 回执缺必填 400 |
| PROCESSING | PROCESSING | 提交未解决回执 + 上报 | 不得转 COMPLETED |
| WAIT_CONFIRM | COMPLETED | 住户确认解决 | — |
| WAIT_CONFIRM | PROCESSING | 住户反馈未解决（REOPEN） | 必须新 Process，旧回执只读 |
| COMPLETED | CLOSED | 归档任务 | — |
| 非终态 | CANCELLED | 规则允许的撤销或管理员取消 | PROCESSING 现场已开始默认不可住户撤销 |

## 6. REOPEN

1. 记录事件 `REOPEN` 到 `WorkOrderLog`。
2. 旧 `WorkOrderProcess` / `WorkOrderReceipt` 全部保留。
3. `cycle_no += 1`，状态回到 `PROCESSING`（原员工继续）或 `PENDING_ASSIGNMENT`（需改派）。默认：若原员工仍在岗且主管未改派 → `PROCESSING` + `NONE`；否则待派。
4. 住户看到完整时间线，而不是被覆盖的单条结果。

## 7. Incident 对状态的影响

- 工单仍按自己的状态走，不合并成一个工单。
- Incident 绑定后，派单引擎优先复用主事件负责人，避免重复派人。
- 住户侧仍是“我的报修”，可补充反馈。
- 授权管理人员处置或重开 Incident 时必须提交原因并写审计；该动作不批量转换成员工单状态，不覆盖回执、反馈或处理历史。

## 8. 并发

- 接单、改派、确认、撤销使用乐观锁 `version` 或条件更新。
- 冲突返回 `WO.CONFLICT`，客户端刷新后再试。
- 派单对同一员工容量使用 Redis 锁 + 数据库容量校验。

## 9. 日志最小字段

`WorkOrderLog`：work_order_id, at, actor_user_id, actor_type, from_status, to_status, process_reason, event_code, request_id, payload_json。

禁止只改状态列不写日志。
