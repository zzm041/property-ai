# ENGINEERING.md — 工程规范（Property AI V1）

路径约定：写成 `core/database.py:107` 这类简写的，一律相对 `backend/app/`；其余路径（`deploy/`、`scripts/`、`reports/`、`docs/`）写全。

本文件是**代码现状的说明书**，不是规划书：每条规则后面都能追到文件与行号，写进来之前都已在
当前提交上验过一遍。产品边界、冻结技术栈、身份与业务铁律的**原始出处**是 `AGENTS.md`
（第 3 节架构约束、第 4 节身份、第 5 节业务）；本文件只做工程视角的收敛与导航，冲突时以
`AGENTS.md` 与 `docs/` 正式规格为准。

---

## 1. 分层与依赖方向

```
api  →  service  →  domain  ←  provider
                    ↑
       repository / model / schema / core（被 service 使用，不反向依赖 service）
```

| 方向 | 允许 | 说明 |
| --- | --- | --- |
| `api → service` | ✅ | 路由只做鉴权、参数校验、错误码映射，不写业务判断 |
| `service → domain` | ✅ | 状态机、规则、AI 字段映射都在 `domain/` |
| `service → provider` | ✅ | 外部系统一律经 provider 接口 |
| `domain → service` | ❌ | 反向 import 会把事务与外部调用拖进领域层 |
| `provider → service` | ❌ | provider 只能依赖 `domain` 与自身 |
| `domain/api → repository` | ❌ | 数据访问只从 service 发起 |

两条反例检查命令（在当前提交上输出均为空，即 0 处违规）：

```bash
grep -rn "from app\.service" backend/app/domain | wc -l     # 期望 0
grep -rn "from app\.service" backend/app/provider | wc -l   # 期望 0
```

同一口径的第三、四条（同样是 0）：

```bash
grep -rn "from app\.api" backend/app/domain backend/app/service | wc -l  # 期望 0
grep -rn "from app\.repository" backend/app/domain | wc -l               # 期望 0
```

`domain/` 只依赖标准库、Pydantic 与同层模块；`provider/` 依赖 `domain/` 与各自 SDK。违反这两条
最常见的方式是「图省事在 domain 里调 service 做校验」——需要校验就把规则搬到 domain，由 service
调用它，不要反向。

## 2. 模块职责表

后端在 `backend/app/`（下列行数取自当前提交，便于判断该往哪加代码）：

| 包 | 行数 | 职责 | 关键文件 |
| --- | --- | --- | --- |
| `api/` | 4285 | HTTP 面：路由、鉴权依赖、错误码出口 | `api/v1/work_orders.py`、`api/v1/health.py`（healthz/readyz）、`api/dependencies/client_ip.py`（D3 客户端 IP 解析） |
| `service/` | 22929 | 事务边界 + 业务流程编排 | `service/work_order.py`、`service/analysis/`（拆包后：`orchestrator.py` / `vision.py` / `validation.py` / `persistence.py`）、`service/auto_dispatch.py` |
| `domain/` | 3533 | 纯规则：状态机、枚举、错误码取值、AI 字段契约 | `domain/work_order.py`、`domain/ai.py`（`FilterReason:43`、`normalize_skill:146`、`rank_candidates:500`）、`domain/iam.py` |
| `provider/` | 3692 | 外部系统适配（每个都有 development 实现） | `provider/factory.py:46-223`、`provider/llm/`、`provider/local_ai/`（自托管适配器拆包：`runtime_control.py` / `http_api.py`）、`provider/file_scanner.py` |
| `worker/` | 1794 | Celery 任务、beat 调度、enqueue 契约 | `worker/celery_app.py:57-61`（`sla-sweep` 调度）、`worker/tasks.py:335/361`、`worker/enqueue.py` |
| `cli/` | 2187 | 运维入口：安装、迁移、provenance、种子 | `cli/customer_install.py`、`cli/delivery_migrate.py`、`cli/build_provenance.py` |
| `repository/` | 16 个文件 | SQLAlchemy 查询，强制带 `tenant_id` / `property_id` | `repository/__init__.py:1`（口径声明）、`repository/employee.py:37` `get_employee_in_tenant` |
| `model/` | 10 个文件 | ORM 表与外键、唯一约束 | `model/work_order.py:778` `IdempotencyRecord`、`:787` 复合唯一 |
| `schema/` | 16 个文件 | Pydantic 请求/响应模型 | `schema/work_order.py:31` 起 `extra="forbid"` |
| `core/` | 15 个文件 | 配置、事务契约、错误码、就绪探针、日志 | `core/config.py`、`core/database.py:107/146`、`core/errors.py:6-189`、`core/readiness.py` |

## 3. 硬约束（每条都有代码位置）

1. **事务在 service**：`core/database.py:107 begin_owned_transaction` / `:146 run_service_transaction`
   是唯一入口，嵌套或越权提交会抛 `TransactionContractError`（`core/database.py:42`）。
   repository 不 commit、不建事务；api 层不 commit。
2. **错误码稳定且集中**：`core/errors.py` 定义 18 个错误码家族（`ErrorCode:6`、`AuthErrorCode:16`、
   `WorkOrderErrorCode:71`、`FileErrorCode:88` … `DevSeedErrorCode:182`），对外统一由
   `AppError`（`:189`）携带 `code / message / status_code / details / request_id`。新增错误先加家族常量，
   不要在服务里拼字符串。
3. **外部调用走 provider**：`provider/factory.py` 是唯一构造点（`build_llm_provider:72`、
   `build_vlm_provider:111`、`build_asr_provider:129`、`build_file_scanner_provider:46` …）。
   service 不得直接 `httpx` 打第三方；provider 必须提供无凭证可跑的 development 实现。
4. **schema 一律 `extra="forbid"`**：目前 `backend/app/schema/` 内 67 处（66 处双引号 + 1 处
   单引号写法 `schema/supervisor_agent.py:6`；例：`schema/work_order.py:31/55/72/81/88`）。
   多传字段直接 422，避免「客户端以为生效」的静默偏差。
5. **幂等键按用户维度**：`IdempotencyRecord` 的唯一约束是
   `(user_id, idempotency_key)`（`model/work_order.py:787`，迁移
   `backend/alembic/versions/20261111_0016_idempotency_key_scope.py:32`）；读路径按用户隔离
   （`repository/work_order.py:206` `get_idempotency`、`repository/property.py:43`），写入在
   `service/work_order.py:1382 / :1555`，唯一冲突由 `:164 _IDEMPOTENCY_UNIQUE` 归类。
   理由见 `docs/adr/ADR-004`。
6. **租户隔离三层**：
   - 存储层：所有租户表带 `tenant_id`，小区级再带 `property_id`（`AGENTS.md:52`）；
   - 查询层：repository 强制把 `tenant_id` 写进 `WHERE`（例：`repository/work_order.py:74/82/86/92`）；
   - 授权层：`OrganizationScope` + `ScopeType`（`domain/iam.py:7`）在 service 内逐资源校验
     （例：`service/work_order.py:818/875/929`）；跨租户与不存在的资源返回同一 error 子树
     （`service/employee.py` 的 `get_employee_in_tenant` 路径）。
7. **AI 无最终控制权**：AI 失败时工单仍创建成功并落 `PENDING_ANALYSIS`（`AGENTS.md:62`）；
   派单只接受规则结果（`AGENTS.md:63`，实现见 `service/auto_dispatch.py:116/269`）。

## 4. 变更流程

| 环节 | 规则（本仓库实际执行） |
| --- | --- |
| 分支 | 主干 `codex/real-sms-onboarding`；功能/修复直接在该分支按任务拆分提交，不另开长期分支 |
| 提交信息 | `<范围>: <做了什么>`，一条提交只做一件事：`D1: scope idempotency keys to the user who sent them`、`PA-168: trust the nginx hop so the client-IP budget is per client`、`pkg: move the local AI adapter into app.provider.local_ai (pure move, no behaviour change)` |
| 提交粒度 | 纯搬移、行为修复、测试修正分开提交；工作树里他人未提交的改动**不得**被 `git add -A` 夹带（用精确路径） |
| 测试门槛 | 全量单测红集**不得新增**。当前基线（提交 `7ba9faa`，2026-11-11）：`29 failed / 3003 passed / 14 skipped`。此前流传的「40 failed」是 t125 修测试环境（`APP_ENV=test` 下 `should_enqueue_ai_on_create` 默认 False）之前的旧数，已作废 |
| 定向测试 | 改哪个模块就跑该模块的测试文件 + 与之相邻的契约测试，再跑全量对比红集 |
| 评审角色 | 开发（实施 + 自测）→ 复核（只读，独立会话/独立成员，不改工作区）→ 发布（搬迁容器与运行时，由专人执行并留 receipt）。同一人不得把自己的实现判为「复核通过」 |

跑基线的规范命令（缺任何一条 env 都会被 `Settings` 校验拦下）：

```bash
APP_ENV=test \
DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_scratch?charset=utf8mb4' \
TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \
PYTHONPATH='D:\property-ai;D:\property-ai\backend' \
./.venv/Scripts/python.exe -m pytest backend/tests/unit -q -p no:randomly
```

集成套件多一道 `ALLOW_DESTRUCTIVE_TEST_DATABASE=true`，且测试库必须是独立实例（`TEST_DATABASE_URL`
与 `DATABASE_URL` 指不同库，否则 `Settings` 直接拒绝启动）。

## 5. 发布与回滚

1. **镜像**：`deploy/Dockerfile` 构建单份 app 包（`rm -rf /app/app` 后自检），`cli/build_provenance.py`
   产出 provenance，`/healthz.build` 回显 `source_sha` / `app_copies` / `shadow_source_tree`。
   三者不一致即视为构建失败，不进入发布。
2. **切容器**：四应用容器（api/worker/publisher/beat）用
   `reports/pa116-cutover/cutover_delivery.py`；`docker compose up` 被
   `deploy/delivery.ps1:54 Assert-NoRetainedRollbackContainers` 故意挡住，因为通用路径会破坏
   rollback 现场。用法：`plan <image>` / `apply <image> [--env KEY=VALUE ...]` / `verify`
   （`reports/pa116-cutover/cutover_delivery.py:246/299`）。`--env` 是必须的：克隆运行中容器的 `Config.Env` 时，
   compose 里后来新增的变量（`TRUSTED_PROXY_CIDRS`、`SMS_IP_LIMIT_PER_HOUR`、
   `FILE_SCANNER_DEMO_ACKNOWLEDGED`）在旧容器里根本不存在，不显式给会被静默丢掉。
3. **rollback 容器保留**：切换时旧容器被改名成 `<name>-rollback-<stamp>`
   （`reports/pa116-cutover/cutover_delivery.py:275/281`），失败可 `rename` 回来（`:291`）。保留策略：**至少留到该版本
   通过冒烟与停血观察**；删除是单独决定，不在发布流程里做。理由见 `docs/adr/ADR-005`。
4. **数据库迁移**：`cli/delivery_migrate.py` 只接受白名单内的起点
   （`:87` `{None, PRE_BOOTSTRAP, IDENTITY_SLA_REVISION, MEDIA_CYCLE_REVISION}`），目标恒为
   `HEAD = "20261111_0016_idempotency_key_scope"`（`:14`），迁移后自检 revision（`:110`）。
   0016 只能在镜像内 `python -m app.cli.delivery_migrate`（`deploy/delivery.ps1:3` 的
   `ValidateSet` 没有 `delivery-migrate`；`:462` 的 `media-cycle-migrate` 只做 0014→0015）。
   客户空库安装走 `cli/customer_install.py`，其常量与 `delivery_migrate.py` 必须同步指向 HEAD。
5. **回滚顺序**：先 `ai-runtime.ps1 down`（或保持运行时不动）→ `cutover_delivery.py` 把
   `-rollback-<stamp>` 改回原名 → `verify` 从 provenance 取回 `source_sha` → 库若是本次迁移引入的
   结构调整，按迁移文件里的 `downgrade()` 回退（0016 的 downgrade 在存在跨用户共享 key 时会
   `RuntimeError` 拒绝，这是设计而不是故障）。

## 6. 文档索引

- 规格：`docs/00-PROJECT-SCOPE.md` … `docs/11-OPERATIONS.md`；AI 派单口径 `docs/05-AI-DISPATCH-SPEC.md`；
  部署与运行时 `docs/09-DEPLOYMENT.md`（含 D6 扫描三态、D9 日志保留、D3 反向代理、PA-166 预算拆账、
  PA-130 资源地板、单一入口）。
- 决策记录：`docs/adr/ADR-001` … `ADR-005`。
- 运维手册：`docs/runbook.md`。
- 验收与证据：`reports/`（例如 `reports/pa116-cutover/RESULT.md`）。
