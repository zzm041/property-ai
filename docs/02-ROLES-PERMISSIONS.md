# 02 — 角色与权限

## 1. 设计原则

- **User 是登录主体**，角色是授权，Employee 是用工身份。
- 一个 User 可同时拥有多个角色，例如既是某房业主，又是物业维修工。
- 组织角色授权必须带范围：`SYSTEM` / `TENANT` / `PROPERTY`。`SELF` 不是组织 Scope Enum。
- 住户能力主要来自有效的 `ResidentHouseRelation`，而不是“在 House 上写了名字”。
- 当前用户访问自己的资源属于 Ownership / Relationship Policy：例如 BindRequest 校验 `resource.user_id == authenticated_user.id`，House 校验有效 `ResidentHouseRelation`。
- 所有鉴权失败返回统一 Error Code，并记录 `request_id`。禁止仅靠前端藏按钮。

## 2. 角色定义

| 角色代码 | 名称 | 授权范围 | 说明 |
| --- | --- | --- | --- |
| `RESIDENT` | 住户/业主 | 有效房屋关系 | 报修、看自己的工单、收公告、知识问答。业主与租户、家属的数据范围相同，部分敏感操作可按关系类型收紧 |
| `STAFF` | 物业工作人员 | `PROPERTY` | 处理分配给自己的工单、签到、填写回执 |
| `SUPERVISOR` | 主管 | `PROPERTY` 或 `TENANT` | 小区主管管理单 Property；Tenant 级主管按角色范围管理多个 Property |
| `PROPERTY_ADMIN` | 小区物业管理员 | `PROPERTY` | 房屋档案、住户审核、员工排班、公告审核发布、SLA、Incident |
| `TENANT_ADMIN` | 企业管理员 | `TENANT` | 管理该企业下所有小区、企业级角色、跨小区报表 |
| `SYSTEM_ADMIN` | 系统管理员 | `SYSTEM` | 租户开通、系统配置、Provider 开关、审计检索。不默认看见业务详情中的业主隐私明文 |

`RESIDENT` 不是“必须单独建号的一种人”，而是登录用户在某房屋上的有效关系所派生的能力。后台仍可写入 `user_role` 便于统一鉴权。

## 3. 范围叠加规则

1. 先认证 User。
2. 收集该 User 的全部 `user_role(scope_type, tenant_id, property_id)`。
3. 收集有效 `ResidentHouseRelation`。
4. 请求必须声明当前操作上下文（header 或路径中的 `property_id`/`tenant_id`）。
5. 无匹配范围则拒绝。禁止“有任意小区管理员角色就能改另一个小区”。
6. 客户端声明的 tenant/property 只用于定位上下文，不能成为权限事实；后端须从用户角色、目标资源及数据库关系重新验证。
7. SELF 类访问不写入 `scope_type`；必须通过资源所有权、有效住户关系或任务分配关系判断。

## 4. 权限点（V1）

权限代码稳定，资源操作用 `resource:action`。

### 4.1 账户

| 权限 | RESIDENT | STAFF | SUPERVISOR | PROPERTY_ADMIN | TENANT_ADMIN | SYSTEM_ADMIN |
| --- | --- | --- | --- | --- | --- | --- |
| 维护自己的登录方式 | Y | Y | Y | Y | Y | Y |
| 发起账号合并（自己的身份） | Y | Y | Y | Y | Y | Y |
| 人工审核身份恢复 | N | N | N | Y | Y | Y |
| 查看安全审计（范围内） | N | N | N | Y | Y | Y |

### 4.2 房产与住户

| 权限 | RESIDENT | STAFF | SUPERVISOR | PROPERTY_ADMIN | TENANT_ADMIN | SYSTEM_ADMIN |
| --- | --- | --- | --- | --- | --- | --- |
| 申请绑定房屋 | Y | N* | N* | N* | N* | N |
| 审核住户绑定 | N | N | N | Y | Y | N |
| 维护楼栋/房屋档案 | N | N | N | Y | Y | N |
| 查看本房信息 | 自己的房 | 工单相关 | 本小区 | 本小区 | 企业 | 按审计需要 |

\* 工作人员若同时也是住户，走住户身份申请，不走员工特权绑定。

### 4.3 人员

| 权限 | STAFF | SUPERVISOR | PROPERTY_ADMIN | TENANT_ADMIN |
| --- | --- | --- | --- | --- |
| 自己签到/改在岗状态 | Y | Y | Y | Y |
| 查看本小区人员状态 | N | Y | Y | Y |
| 维护员工档案/技能/容量 | N | N | Y | Y |
| 调整他人在岗（请假代录） | N | Y | Y | Y |

签到还必须满足：Employee 归属当前 Tenant，且在目标 Property 存在当前有效的 `EmployeeProperty(status, valid_from, valid_to)`。角色本身不能替代该工作范围关系；篡改 `property_id` 必须被拒绝。

### 4.4 工单

| 权限 | RESIDENT | STAFF | SUPERVISOR | PROPERTY_ADMIN |
| --- | --- | --- | --- | --- |
| 创建自己的工单 | Y | 仅当自己是住户 | 同左 | 同左 |
| 查看自己的工单 | Y | 被分配的 | 本小区 | 本小区 |
| 接单/拒单/处理 | N | 被分配人 | 可接管 | 可接管 |
| 完成并提交回执 | N | 被分配人 | 可代管 | 可代管 |
| 关闭异常 / 选后续方案 | N | N | Y | Y |
| 人工派单/改派 | N | N | Y | Y |
| 撤销未处理工单 | 自己的 | N | Y | Y |

### 4.5 Incident / 公告 / 知识 / SLA

| 权限 | STAFF | SUPERVISOR | PROPERTY_ADMIN | TENANT_ADMIN |
| --- | --- | --- | --- | --- |
| 查看 Incident | 参与者 | Y | Y | Y |
| 处置 Incident 主事件 | N | Y | Y | Y |
| 创建公告草稿 | N | Y | Y | Y |
| 审核发布公告 | N | Y | Y | Y |
| 管理知识库 | N | N | Y | Y |
| 配置 SLA 策略 | N | N | Y | Y |
| 查看当前运营统计 | N | N | Y | Y |

住户：只读已发布且命中范围的公告；知识助手只检索对本住户可见的知识。

公告审核链：创建草稿 `announcement:draft` → 提交审核 → 审核发布 `announcement:publish`（可驳回，驳回需填理由）。
F-4 / t210：`announcement:publish` 原先只挂 PROPERTY_ADMIN 及以上，小区只配主管时公告永远停在
`PENDING_REVIEW`。现按上表把发布权给 SUPERVISOR（主管就是现场审核人），迁移
`20261112_0020_supervisor_announcement_publish` 幂等补授权；`PROPERTY_ADMIN` / `TENANT_ADMIN` 保留原权限。

## 5. 数据隔离

| 对象 | 隔离键 |
| --- | --- |
| 企业配置、知识默认集 | `tenant_id` |
| 小区、房屋、员工排班、工单、Incident、公告 | `tenant_id` + `property_id` |
| 住户关系 | `house_id` → 所属 property/tenant |
| 系统审计、租户清单 | system 角色 |

默认查询必须带隔离条件。禁止“先全表再内存过滤”作为唯一防护。

顺序 BIGINT ID 可被猜测，因此任何按 ID 读取或修改资源的 API 都必须同时执行 Scope 与 Ownership/Relationship 校验，防止 IDOR。

## 6. 小程序信息架构（同一应用）

登录后根据角色集合显示 Tab/入口：

- 仅住户：报修、我的工单、公告、助手、我的房屋/账户
- 工作人员：任务、签到、工单处理
- 主管：调度看板、异常工单、人员状态
- 管理员：审核、档案、公告、SLA、人员、当前运营统计
- 多角色用户：底部或工作台切换“住户 / 工作”视图，不切换账户

## 7. 审计要求

以下操作必须写 AuditLog：登录失败锁定、改密、换手机、换微信、合并、身份恢复、角色变更、住户审核、人工派单、异常工单决策、公告发布、SLA 变更、知识发布。

2026-09-23 负责人明确调整“不锁定”（见01-PRD、07-SECURITY-PRIVACY）：上列登录失败锁定属于已被替代的历史要求，不新增该锁定流程；既有审计历史保留，其余操作的强制审计要求不变。此处不取消现有验证码挑战、尝试上限或频率限制。
