# runbook — 交付栈运维手册

适用：`property-ai-delivery-check`（api / worker / publisher / beat + mysql / redis / rabbitmq /
milvus + 自托管 AI 运行时）。命令在 `D:\property-ai` 下执行；本机 PowerShell 用
`powershell -NoProfile -ExecutionPolicy Bypass -File …`（无 pwsh 7，ExecutionPolicy 为 Restricted）。
**不要**手工起适配器，也不要绕过 `Assert-NoRetainedRollbackContainers`（`deploy/delivery.ps1:54`）。

## 1. 启动顺序

```text
① 依赖：docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml up -d mysql redis rabbitmq etcd minio milvus
② 应用：同上 up -d api worker publisher beat
③ 运行时：deploy\ai-runtime.ps1 up        # ollama 11435 + 适配器 11437，唯一入口
④ 就绪：curl http://127.0.0.1:8093/api/v1/readyz
⑤ provenance：reports\pa116-cutover\cutover_delivery.py verify
```

- ② 之后才有 `/healthz`；mysql/redis 未起时 api 会反复重启，先 `docker ps` 再排。
- ③ 的 `up` 会重写 `tmp\delivery-work-ai-runtime\config.json`（预算与 evidence 清单都在里面），手工改的键下次 `up` 就没了。
- ④ 必须 `ready`：`degraded` 见 F3，`not_ready` 见 F4。⑤ 必须四容器同 digest、`app_copies` 单份、`shadow_source_tree=null`。

## 2. 健康检查怎么读

**应用 `/api/v1/readyz`**（`backend/app/api/v1/health.py:30`）：外壳 `{"request_id":…,"data":{…}}`，**看 `data`**。

| 字段 | 判读 |
| --- | --- |
| `status` | `ready` / `degraded`（HTTP 200）/ `not_ready`（503） |
| `ai_credentials` | `status=configured`，endpoint 必须是 `host.docker.internal:11437/v1` 这类本地端点 |
| `media_pipeline` | `scan_mode` 三态 `real` / `demo_stub` / `unavailable`；只有 `real` 才 `media_becomes_usable=true` |
| `dependencies` | mysql/redis/rabbitmq/milvus 应为 `up`；llm/vlm/asr 常为 `not_checked`，不代表坏 |
| `queue.depth` | 持续上涨＝worker 没在消费 |

常见 `degraded` 只有一个原因：`media_pipeline.demo_stub_unacknowledged=true`（`backend/app/core/readiness.py:175`）——扫描器是演示桩且未显式确认。

**应用 `/healthz`**（`backend/app/api/v1/health.py:19`）：看 `build.source_sha` / `app_copies` / `shadow_source_tree`，确认跑的是哪份代码。

**适配器 `http://127.0.0.1:11437/healthz`**（`/readyz` 同载荷，`backend/app/provider/local_ai/http_api.py:43/47`）；Bearer 取自 `tmp\delivery-work-ai-runtime\internal-token.txt`：

| 字段 | 判读 |
| --- | --- |
| `reservation_gate` | 必须 `READY`；`BLOCKED_*` ＝ 有未完成调用或证据校验失败 |
| `budget` | `limits` / `call_baseline` / `counted` / `counted_records` / `evidence_excluded`；`call_baseline+counted ≥ limits` 即无额度 |
| `floors` | 三个地板生效值与来源（`default` 或 `env:<变量名>`） |
| `resource_floor` | `floor_mib` / `observed_mib` / `short_by_mib`（正数＝还差多少） |

## 3. 五类故障：症状 → 定位 → 处置

**F1 分析全失败 `FAILED / AI.UNAVAILABLE`**

```bash
docker logs --since 10m property-ai-delivery-check-worker-1 | grep provider_failed
tail -3 tmp/delivery-work-ai-runtime/calls/pre-reservation-failures.jsonl
```

看末行 `stage` / `reason_code`：`BUDGET_AND_LEDGER / BUDGET_REJECTED` → 无额度，检查适配器 `budget`，确认
`evidence_records` 已声明历史证据（清单 `tmp\delivery-work-ai-runtime\evidence-records.txt`）后 `deploy\ai-runtime.ps1 up`
生效；**不要**抬 `limits` 来凑。`MODEL_RESIDENCY_AND_RESOURCE / RESOURCE_ADMISSION_REJECTED` → 见 F2；连接被拒 → 见 F5。`FAILED` 不自动重试，处置后需重新触发分析。

**F2 资源地板拒绝（`short_by_mib` 几十 MiB）**

```bash
curl -s http://127.0.0.1:11437/readyz -H "Authorization: Bearer $(cat tmp/delivery-work-ai-runtime/internal-token.txt)"
nvidia-smi --query-gpu=memory.free --format=csv
```

核对「地板 − 实测 = short_by_mib」。32GB/16GB 机器上 27B(q3) 常驻只剩 ~1950 MiB 空闲显存，默认地板 2000 ⇒ 必然
差几十 MiB，属容量问题（`docs/09-DEPLOYMENT.md:228`）。处置：先释放显存再重试；确需临时放行按 `docs/09-DEPLOYMENT.md:211` 用 `LOCAL_AI_*_FLOOR_MIB` 覆盖并记录原因与时长，排查完恢复默认再 `up`。

**F3 readyz `degraded`：媒体链是演示桩**

```bash
curl -s http://127.0.0.1:8093/api/v1/readyz | grep -o '"media_pipeline":{[^}]*}'
```

`scan_mode=demo_stub` 且未 ack ⇒ 附件能传但不会被扫描、媒体永不变可用。要跑演示就设 `FILE_SCANNER_DEMO_ACKNOWLEDGED=true`
并重启 api；要真扫描就配 clamav（`FILE_SCANNER_PROVIDER=clamav` + 私网 `FILE_SCANNER_HOST`），见 `docs/09-DEPLOYMENT.md:117`。

**F4 readyz `not_ready`（503）：关键依赖 down**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8093/api/v1/readyz
docker ps --format '{{.Names}}\t{{.Status}}'
docker logs --since 10m property-ai-delivery-check-mysql-1 | tail -20
```

mysql / redis / rabbitmq / milvus 任一 down 即 503：先起容器，再核 `DATABASE_URL` / `REDIS_URL` 是否指对。**不要**改 `readiness_timeout_seconds` 掩盖慢库。

**F5 适配器起不来 / receipt 与实际进程不一致**

```bash
deploy\ai-runtime.ps1 status
netstat -ano | findstr :11437
git rev-parse HEAD
```

- `frozen work AI adapter hash mismatch`：改过适配器却没同步钉值。按 `git show <commit>:backend/app/provider/local_ai/runtime_control.py` 的 LF 字节算 sha256，同步 `deploy\work-ai-runtime.ps1:20 $SourceCommit` 与 `:25 $ExpectedAdapterSha256` 后 `up`。
- receipt 的 `launcher_pid`/`listener_pid` 与 `netstat` 不一致：有人手工起过。`Stop-Process` 掉手工进程 → `deploy\ai-runtime.ps1 down` → `up`。
- `a model is still resident`：先 `POST /api/generate {"model":"<当前模型>","keep_alive":0}` 卸载；无法判定的 barrier 用 `deploy/ai-runtime.ps1 resolve-unknown`（动作清单见 `:3`）。

## 4. 回滚步骤

```text
① 镜像：cutover_delivery.py 把 <name>-rollback-<stamp> 改回原名（名字见 cutover-<stamp>.json），再 verify
② 运行时：deploy\ai-runtime.ps1 down；换代码先改钉值再 up
③ 数据库：升用镜像内 python -m app.cli.delivery_migrate，回退按迁移文件 downgrade()（0016 在有跨用户共享 key 时拒绝，属设计）
④ 证据：cutover-<stamp>.json、readyz 输出、失败日志落到 reports/ 再收工
```

- rollback 容器不删（`docs/adr/ADR-005`）：新版本通过冒烟与停血观察后再按精确 ID 删。
- 任何一步失败不要连续重试超过 2 次，保留现场（`docker inspect` + 日志）再决定。

## 5. 本地跑测试

```bash
python scripts/ops/unit_gate.py        # 判据 = 红集 ⊆ scripts/ops/known-red.txt，不比计数
python scripts/ops/check_layering.py   # 依赖方向静态闸（--report 看基线，--tree 只查 domain 顶层归属）
```

`unit_gate.py` 现在先跑 `scripts/ops/check_script_imports.py`（S7-3）：`scripts/` 下每个 `app.*`
import 都必须真能 `find_spec` 到，否则 gate 直接 exit 1（`--skip-script-imports` 只给正在改树的
人用）。pytest 从不 import `scripts/`，所以这条以前没人管：包结构一动，runbook 脚本里的死
import 要等到真用它那天才炸。单独跑它也是自检清单的一项：

```bash
python scripts/ops/check_script_imports.py            # imports checked N failures 0
python scripts/ops/check_script_imports.py --self-test # 造 app.service.gone，必须报 no spec
```

env 缺一不可：`APP_ENV=test` + `DATABASE_URL` + `TEST_DATABASE_URL`（两库不同），缺了 gate 直接 exit 2。**`APP_ENV` 坑**：`Settings(_env_file=None)` 仍读进程环境，`APP_ENV=test` 会改 `should_enqueue_ai_on_create`（`backend/app/core/config.py:238-245`）；fixture 须显式声明建模环境，例 `test_durable_tasks_default_104.py::_settings`。

### 5.1 integration 需要一个**已迁移**的测试库（PA-118）

`deploy/docker-compose.yml` 的 `mysql-test` 服务（profile `test`，tmpfs）只创建空库
`property_ai_test`，**不跑迁移**。直接跑 integration 的后果是每个用例一条
`pymysql.err.ProgrammingError: (1146, "Table 'property_ai_test.users' doesn't exist")`，
API 层则变成 500 `SYS.INTERNAL_ERROR`——看起来像上百个缺陷，实际只是一个没迁移的 schema。

```bash
# 1) 起一次性测试库（空库）
docker compose -f deploy/docker-compose.yml --profile test up -d mysql-test

# 2) 建库 → 迁到 head（幂等；--check 只体检，不迁移）
APP_ENV=test \
TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \
#    非交互环境必须带 --yes-i-mean-it：脚本先确认目标确实是一次性测试库（PA-174）。
#    目标须为本机 host、名字含 test 且不含 prod/staging/live/delivery 等分段；
#    远端 host 一律拒（放行请显式给 PREPARE_TEST_DB_ALLOWED_HOSTS=host1,host2）。
python scripts/ops/prepare_test_database.py --yes-i-mean-it
```

库已经存在表但 `alembic_version` 为空（例如手工 `DELETE FROM alembic_version`、或从旧快照恢复）
时，直接 upgrade 会撞 `(1050, "Table 'tenants' already exists")`。脚本会先判断结构：与 head 模型
一致就 stamp 到 head，否则按迁移脚本推导出的表集合推断最近 revision 再 stamp，然后照常 upgrade。
推断不出来时不会猜，而是给两条可执行选项（`down -v` 重建，或 `--stamp <revision>`）；`--check`
的措辞与真实行为一致，不会再出现「检查说能跑、跑起来就炸」。

脚本的三段式与 `app.cli.customer_install` 同源，因为裸 `alembic upgrade head` 建不出这个
schema：0009/0011 只认 AUTH001、delivery 精确目标或 `CustomerInstallAuthority` 三种身份；
0014 又要求**已经存在** SYSTEM_ADMIN（基线溯源）。所以流程是
`upgrade → 0012` →（若无 SYSTEM_ADMIN）bootstrap 一个基线管理员 → `upgrade → head`，
切换完成后删除该管理员，让 schema 回到**数据为空**（integration 的清理 fixture 会删
`users` 但不删 `user_passwords` 等子表）。

`APP_ENV=test` 的进程（含 pytest 收集）在启动期就会体检：库连不上 → `TEST_DATABASE_UNREACHABLE`；
连上但缺表 → `TEST_DATABASE_SCHEMA_MISSING`，消息里带目标、缺的表和修复命令。要绕过体检
（例如你自己在测空 schema 的错误路径）设 `REQUIRE_TEST_DATABASE_SCHEMA=false`。

已知状态（2026-11-11，head = `20261111_0018_content_fingerprint_loose`）：空库从
`61 failed / 16 passed / 18 skipped / 228 errors` 变成 **`41 failed / 265 passed / 17 skipped /
0 errors`**（466 s，Redis+Milvus+MinIO 都起）。41 条的定性：
- **19 条把历史 head 写死**（`test_ai_migration.py`、`test_employee_migration.py`、`test_migrations.py`、`test_identity_schema.py`、
  `test_official_auth_runtime.py` 的 `assert get_current_head() == "20260917_0007"` 一类），与 schema
  无关，任何环境必红 —— **测试过时**，需要跟着 0018 更新；
- **13 条真检索**需要 embedding 服务在跑（`test_day4_async_claim.py`、`test_day4_milvus_rebuild_acl.py`、
  `test_day4_mysql_restore_relations.py`、`test_knowledge_runtime.py`、`test_milvus_rag.py`）——
  **环境缺件**：Milvus 起了但没有 embedding 服务，索引建不出来；
- **8 条测试环境默认值**：`FILE_SCANNER_PROVIDER` 默认 `unavailable`（媒体内容 `not ready for use`，
  `409`），设 `FILE_SCANNER_PROVIDER=development FILE_SCANNER_DEMO_ACKNOWLEDGED=true` 后这 8 条只剩 4 条；
- **1 条测试过时**：`test_observation_clarification_runtime.py:38` 仍期望 `required_skill="maintenance"`，
  PA-167 归一化后是 `MAINTENANCE`；
- **4 条待跟进**（加扫描器后仍红）：`test_stale_result_does_not_override_human_cancel`
  （analysis 为 None）、`test_human_override_records_recommendation_difference`
  （`Employee is not eligible for dispatch`）、`test_confirm_closes_and_system_admin_is_denied`
  （403 vs 期望 409）、`test_concurrent_revoke_has_one_success_and_one_not_found`
  （`threading.BrokenBarrierError`，并发用例自身不稳）。
逐条证据见 `D:\AI\review\evidence\t171-pa118-test-schema.md` §4/§5。

### 5.2 证据归档 ≠ 放宽限额（PA-179）

适配器的账本只有一把尺子：`calls/call-*/reservation.json` 里 `budget_counted` 的记录累加后
不得超过 `limits`，够不到就 `BUDGET_REJECTED`。问题在于**评审要的正是这些 call 目录**，于是
「把证据放回去」和「把预算用光」变成同一个动作——恢复证据直接打爆窗口，而失败对外只表现为
`AI.UNAVAILABLE`，看起来像模型坏了。

拆账做在**声明**层，不做在限额层：

- `tmp/delivery-work-ai-runtime/evidence-records.txt`（每行一个 `call-NNN`）是权威声明，
  `deploy/work-ai-runtime.ps1` 每次 `start` 都把它编译进 `config.json` 的 `evidence_records`；
  直接改 `config.json` 会在下次启动被覆盖。
- 被声明的 call 仍留在 `calls/` 里（启动闸与评审照旧要读它），只是从 `counted` 移到
  `evidence_excluded`：`/healthz` 的 `budget` 块同时给 `counted` / `evidence_excluded` /
  `evidence_records` 三个字段。
- `limits` 与 `call_baseline` 一个字节都不动。归档不是「多给几次调用」，而是「别把已评审的
  调用再算一次」。

```powershell
# 把一次已评审的调用标成 evidence-only，并立刻重启适配器让声明生效
deploy\ai-runtime.ps1 evidence-archive -Call call-001 -Reason "t165 review evidence"

# 只查账、不写盘
python scripts/ops/ai_evidence_archive.py --call call-001 --reason "dry run" --dry-run
python scripts/ops/ai_evidence_archive.py --list
```

每次归档都会往 `evidence-ledger.jsonl` 追加一行：`archived_at`、`call`、`reason`、三个文件的
`sha256`（`reservation.json` / `result.json` / `input.json`）、`record_sha256`、以及内容变更时的
`supersedes`。**工具从不删除或移动 call 目录**——删证据等于消灭审计线索，那是掩盖不是拆账；
要撤销声明就从 manifest 里删掉那一行（下一次 `start` 生效，账面上它会回到 `counted`）。

**重启会把 manifest 抬回 counted，脏账必须先 reconcile。** manifest 与 `calls/` 是两个来源，
声明了但目录已经不在的行（幽灵声明）在运行期看不出来——`/healthz` 的 `counted` /
`evidence_excluded` 只统计在盘的 reservation；可一旦真正 `start`，`work-ai-runtime.ps1` 会按
manifest 重建 `config.json`，那些不存在的 call 就被重新算成 evidence，账面上凭空多出一批声明。
所以：

- `/healthz` 的 `budget` 额外给 `declared_records`（manifest 声明数）与 `ghost_records`
  （声明了但盘上没有的条数）；`declared_records` 明显大于 `evidence_excluded` 就是脏账。
- `python scripts/ops/ai_evidence_archive.py --reconcile` 把 manifest 与 `calls/` 求交，移出幽灵行，
  并在 `evidence-ledger.jsonl` 追加一条 `event=reconciled`（含被移除的 id 与原因）；`--dry-run` 只报不写，
  `--list` 末尾固定打印 `declared=… on_disk=… ghosts=…` 三数。
- `deploy\ai-runtime.ps1 up` 在交给 launcher 之前会自动跑一次对账，`ghosts>0` 时打印 WARNING，
  不再静默启动。

演示（隔离运行时 `tmp/t191-demo-runtime`，`limits.qwen=1`，同一请求体三连）：
`200` → `503 BUDGET_REJECTED` → 归档 `call-001` → 重启 → 同一请求 `200`，且
`counted.qwen 0 / evidence_excluded 1`。整段日志见 `D:\AI\review\evidence\t191-evidence-ledger.md`。

## 6. 合并前必须绿（CI 门槛）

`.github/workflows/ci.yml` 有两条必过的 job。**两条都绿才允许合并**（分支保护建议：把
`unit gate (windows, red ⊆ known-red)` 与 `cross-platform checks (ubuntu)` 都设为 required status check，
并禁止自行审批自己的 PR——合并权在复核人手上）。

| job | runner | 守什么 | 红了怎么读 |
| --- | --- | --- | --- |
| `unit gate (windows, red ⊆ known-red)` | `windows-latest` | 全量单测的红集必须 ⊆ `scripts/ops/known-red.txt`；`scripts/` 里每个 `app.*` import 必须能解析 | 新红逐条打在 `NEW RED:`，并汇总进 job summary（passed/failed/skipped/errors + red 与基线数）；gate 无法解析结果时 fail-closed，exit 2 |
| `cross-platform checks (ubuntu)` | `ubuntu-latest` | 4 个纯 Python 用例在 POSIX 上可跑、`check_script_imports.py` 全通、`check_layering.py` 通过 | 采集失败说明有模块在顶层 import 了 Windows-only 模块（t173 的 `msvcrt` 就是这样被抓到的） |

为什么 gate 跑 Windows 而不是 ubuntu：`known-red.txt` 的基线是在 Windows 上采集的，
ubuntu 上另有 35 条「只在 Windows 成立」的用例（`msvcrt.locking` 句柄锁、`GlobalMemoryStatusEx`
资源地板、PowerShell 自托管栈、`winreg`）必然失败。把它们放进 gate 只会逼人加白，
于是闸门退化成「跟环境差异比大小」。便携性单独由上面第二个 job 守。

两条 job 都需要一个**已迁移**的 MySQL（`app.main` 在 `APP_ENV=test` 下启动即体检 schema）：
ubuntu job 用 service container（`mysql:8.0`）起空库；windows runner 不支持 service container，
改用 `actions-setup-mysql` 装本地 MySQL 8。两者随后都跑同一个 `scripts/ops/prepare_test_database.py`
（三段式，见 §5.1），**不手工建表、不 `stamp` 到某个中间版本**。

本地等价模拟（不想等 CI 时）：

```bash
# 1) 起一个空库并迁到 head（重复跑是幂等的）
docker run -d --name ci-mysql -p 3337:3306 --tmpfs /var/lib/mysql   -e MYSQL_ROOT_PASSWORD=root-test-pw -e MYSQL_DATABASE=property_ai_test   -e MYSQL_USER=property_test -e MYSQL_PASSWORD=change-me-test mysql:8.0
APP_ENV=test TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_test?charset=utf8mb4'   python scripts/ops/prepare_test_database.py --yes-i-mean-it

# 2) 跑与 CI 相同的两道闸
APP_ENV=test DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_scratch?charset=utf8mb4' TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_test?charset=utf8mb4'   python scripts/ops/unit_gate.py --summary-file /tmp/unit-gate.md
python scripts/ops/check_script_imports.py && python scripts/ops/check_layering.py
```

`--summary-file` 会在决定退出码**之前**写摘要，所以红的 run 也留得下三数——CI 里传的是
`$GITHUB_STEP_SUMMARY`，本地想看就传任意路径。

## 7. RAG 运行时：未启用、未验证

现状：**交付栈没有 RAG 运行时**。11436/11438 无监听，`POST /api/v1/assistant/query` 恒返回 `{"status":"knowledge_unavailable"}`，引用与权限过滤因此从未被真实验证（`reports/flow-matrix-20261111.md` 记为阻塞项）。

`deploy/rag-runtime.ps1` 的失败都带稳定码，不静默：

| 现象 | 输出 | 含义 |
| --- | --- | --- |
| 无 receipt | `RAG_RUNTIME_DISABLED: … has no current receipt under …` | 本机从未启动过 |
| 有 receipt 但 11436 无监听 | `RAG_RUNTIME_DISABLED: receipt … exists but nothing listens on 11436` | 上次运行已死，receipt 陈旧 |
| RAG Ollama receipt 的 pid 不存在 | `RAG_RUNTIME_DISABLED: … records pid <n>, which is not running` | 上游 ollama 已停，绑定无从建立 |
| 候选文件与钉值不符 | `frozen RAG adapter hash mismatch: pinned=… actual=… path=…` | 钉值是 `git archive` 字节形态（`core.autocrlf=true` 时为 CRLF）；`git show \| sha256sum` 得到 LF 形态，两者永不相等 |

启用前清单（缺一项就别启动；逐项确认后再 `deploy\rag-runtime.ps1 start`）：

1. 11438：`deploy\rag-ollama-runtime.ps1 start` 成功，`tmp\delivery-rag-ollama-runtime\current.json` 的 `pid` 存活、`host=127.0.0.1:11438`、`no_cloud=true`、`context_length=4096`、`concurrency=1`。
2. 模型：`$ModelRoot` 下有 embedding/reranker 权重，RAG 用的 qwen 与 `api/ps` 里的一致。
3. 11436：`deploy\rag-runtime.ps1 start` 后 `netstat` 见到 `127.0.0.1:11436 LISTENING`，且 `status` 返回 `rag_enabled=true`。
4. 端点：`EMBEDDING_ENDPOINT` / `RERANKER_ENDPOINT` 指向 11436，适配器 `/healthz` 的 `models` 三项非空。
5. 应用侧：`GET /api/v1/readyz` 的 `dependencies` 里 `embedding`/`reranker` 由 `not_checked` 变 `up`，再跑一条 `assistant/query` 看 `citations` 非空、跨租户问题为空。

## 8. 内容指纹的两层口径（PA-169）

报修内容有两条身份，用途不同，**不要混用**（`backend/app/domain/ai/rules.py`）：

| 口径 | 归一化 | 存哪 | 谁在读 |
| --- | --- | --- | --- |
| 严格 | 标点/空白=词边界（`厨房水管，漏水` → `厨房水管\|漏水`） | `work_orders.content_fingerprint`、`ai_call_logs.content_fingerprint` | **隔离判定**（`AI.INPUT_QUARANTINED`，24h 窗口，按租户） |
| 松散 | 标点/空白全删（`厨房水管，漏水` → `厨房水管漏水`） | `work_orders.content_fingerprint_loose` | **仅去重**（10 分钟内同住户同 house 视为同一单） |

- 隔离用严格口径：`不需要维修` 与 `不需要，维修` 语义相反，合并会误伤（t152 反例）。
- 去重用松散口径：同一句换个逗号仍是同一份报修，严格口径会让住户绕过重复窗口（t154）。
- 否定词不靠词表：语义由「标点=边界」保证；`_FINGERPRINT_NEGATIONS` 这类词表在 t154 删除（对任何比较都不生效，属死代码）。
- 历史行两列为 `NULL` 不回填：去重窗口只有 10 分钟、隔离窗口 24 小时，回填无收益且要在 SQL 里复刻 NFKC。
