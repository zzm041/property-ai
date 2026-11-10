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
python scripts/ops/check_layering.py   # 依赖方向静态闸（改 domain 结构前先跑，--report 看基线）
```

env 缺一不可：`APP_ENV=test` + `DATABASE_URL` + `TEST_DATABASE_URL`（两库不同），缺了 gate 直接 exit 2。**`APP_ENV` 坑**：`Settings(_env_file=None)` 仍读进程环境，`APP_ENV=test` 会改 `should_enqueue_ai_on_create`（`backend/app/core/config.py:238-245`）；fixture 须显式声明建模环境，例 `test_durable_tasks_default_104.py::_settings`。

## 6. RAG 运行时：未启用、未验证

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
