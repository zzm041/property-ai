# ADR-005：切容器时保留 `-rollback-<stamp>` 容器，而不是删旧重建

- 状态：已采纳
- 日期：2026-11-11（记录 `reports/pa116-cutover/` 的既有做法）
- 相关：`reports/pa116-cutover/RESULT.md`、`deploy/delivery.ps1:54`

## 背景

四应用容器（api/worker/publisher/beat）不是无状态玩具：`beat` 挂着 `beat_state` 卷、
worker 带长任务与队列租约，容器里有一次性注入的 env（trusted proxy 网段、限额、扫描器 ack）
与 healthcheck。用 `docker compose up -d` 换镜像时，compose 会按声明**重建**容器：旧容器直接
消失，新容器的 env 以 compose 文件为准——那份文件当时并没有全量写全（有些变量是后来才加进
compose 的），于是「换镜像」顺手把配置换掉了。

`reports/pa116-cutover/RESULT.md` 记下过这个代价：`deploy/delivery.ps1 release` 被
`Assert-NoRetainedRollbackContainers` 拒绝，因为项目下已经积了 21 个 `*-rollback-<ts>`
保留容器——而它们正是之前几次切换「唯一的退路」。

## 决策

1. 切容器只走 `reports/pa116-cutover/cutover_delivery.py`：`docker inspect` 读运行中容器的
   实际配置（env、Cmd、WorkDir、User、RestartPolicy、ExtraHosts、labels、volume、
   healthcheck、端口映射），照它 clone 一个新容器，`start` 并等健康，**最后**才
   `stop` 旧容器并 `rename` 成 `<name>-rollback-<stamp>`（`reports/pa116-cutover/cutover_delivery.py:275/281`）。
2. 旧容器不删：失败时 `rename` 回来即可回到上一版（`:291`）；成功也保留，留作证据与退路。
3. `docker compose up` 路径用 `Assert-NoRetainedRollbackContainers`
   （`deploy/delivery.ps1:54`，在 `:291/:304/:323/:331` 处被调用）故意挡住，避免有人绕过硬泛化
   的 compose 操作把现场毁掉。
4. 新一代 env 必须显式给：`--env KEY=VALUE`（`reports/pa116-cutover/cutover_delivery.py:246` 的 `cmd_apply`）。
   因为 clone 的是旧容器的 `Config.Env`，compose 里后来新增的变量在旧容器里不存在，
   不显式传就会静默缺失（实测：第一次漏传 `FILE_SCANNER_DEMO_ACKNOWLEDGED`，readyz 直接
   `degraded`）。
5. 每次切换写记录 `cutover-<stamp>.json`（含 `env_overrides`、rollback 名、镜像 digest），
   并跑 `verify` 确认四容器同 digest、provenance 单份 app（`reports/pa116-cutover/cutover_delivery.py:299`）。

## 替代方案

| 方案 | 未采纳原因 |
| --- | --- |
| `docker compose up -d` 换镜像 | 会重建容器：旧配置与旧现场同时消失，失败无退路；且当时 compose 并未写全 env |
| 原地 `docker compose stop/rm` + 重建 | 同上的问题，还多一段服务不可用的空窗 |
| 用 tag 回滚镜像（保留上一版镜像，删容器） | 镜像能回，但 env/healthcheck/卷挂载的差异不跟着回；且没有「上一版容器的实际配置」可比对 |
| 蓝绿两套 compose 工程 | 需要两套网络与卷，迁移成本远大于四容器克隆；V1 单机不值得 |

## 后果

- 正向：切换失败的最小代价是「把 rollback 容器改回原名」，不需要重新构建或重放迁移。
- 正向：`verify` 能从 provenance 读出 `source_sha` / `app_copies` / `shadow_source_tree`，
  「跑的是哪份代码」不靠猜（单份 app，`shadow_source_tree=null`）。
- 代价：容器数量会累积（当前项目下就有数十个 `-rollback-*`），清理必须是单独决定：
  先确认新版本通过冒烟与停血观察，再按精确 ID 删除，不许批量 `docker container prune`。
- 代价：切换工具要懂容器细节（clone 配置、写 env 文件、等 healthy），不能用 compose 一行替代；改这一层要重跑 `verify`。
## 证据（文件:行号）

- `reports/pa116-cutover/cutover_delivery.py:3-7`（docstring：为什么不用 compose）、
  `:246`（`cmd_apply`）、`:275`（rollback 命名）、`:281`（rename）、`:291`（改回原名）、
  `:299`（`cmd_verify`）、`:214`（`apply_env_overrides`）
- `deploy/delivery.ps1:54`（`Assert-NoRetainedRollbackContainers`）、`:291/:304/:323/:331`（调用点）
- `reports/pa116-cutover/RESULT.md`（21 个保留容器、精确 ID 处理的要求）
- `backend/app/cli/build_provenance.py`（`app_copies` / `shadow_source_tree` 的来源）
