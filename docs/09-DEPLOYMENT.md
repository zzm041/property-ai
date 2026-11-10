# 09 — 部署与版本冻结

## 1. 部署形态

Production V1：**单机或单台云主机 + Docker Compose + Nginx**。  
禁止在未批准前改为 Kubernetes、云厂商专用编排或微服务拆分。

```text
[微信客户端]
      │
      ▼
[Nginx :443] ── reverse_proxy ── [FastAPI :8000]
                      │
                      ├── [Celery worker]
                      ├── [Celery beat]（SLA / 超时重派 / 通知重试）
                      ├── [MySQL 8.0]
                      ├── [Redis 7.4]
                      ├── [RabbitMQ 3.13]
                      ├── [Milvus standalone 2.5]
                      └── [本地或 S3 兼容对象存储]
```

## 2. 冻结版本

选择 **当前稳定且互相兼容** 的版本。原则：Python 3.12 生态最完整；Milvus 跟 2.5 + pymilvus 2.5，不追 3.0；Redis 用 7.4 镜像 + redis-py 5；Celery 用 5.5 线（对 AMQP 最稳）。

### 2.1 语言与 Python 包

写入 `backend/pyproject.toml`，实现时不得擅自浮动 major。

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python Compatibility | `>=3.12,<3.13` | 项目语言兼容范围 |
| Windows Host Python | 3.12.10 | 本地开发与 Day 1 `.venv` |
| FastAPI | 0.115.12 | API |
| Starlette | 0.41.3 | FastAPI 依赖锁定 |
| Pydantic | 2.10.6 | Schema |
| pydantic-settings | 2.7.1 | 环境变量配置 |
| Uvicorn | 0.32.1 | ASGI |
| SQLAlchemy | 2.0.36 | ORM 2.x |
| Alembic | 1.14.0 | 迁移 |
| PyMySQL | 1.1.1 | 应用同步 SQLAlchemy Session + Alembic |
| cryptography | 44.0.0 | MySQL 8 caching_sha2 |
| Redis (redis-py) | 5.2.1 | 缓存/锁 |
| Celery | 5.5.3 | Worker |
| kombu | 5.5.4 | Celery 传输 |
| billiard | 4.2.1 | Celery 进程 |
| pymilvus | 2.5.14 | Milvus 客户端，匹配 2.5 服务 |
| httpx | 0.28.1 | Provider HTTP |
| PyJWT | 2.10.1 | Token |
| argon2-cffi | 23.1.0 | 密码哈希（Argon2id） |
| python-multipart | 0.0.20 | 上传 |
| orjson | 3.10.15 | JSON |
| structlog | 24.4.0 | 结构化日志 |
| pytest | 8.3.4 | 测试 |
| pytest-asyncio | 0.25.2 | 异步测试 |
| pytest-cov | 6.0.0 | 覆盖率 |

若安装解析与 FastAPI 0.115.12 的依赖范围冲突，允许在 **不更换框架** 的前提下把 Starlette 调整到 FastAPI 声明的兼容范围，并回写本文。

Production V1 本周不使用 aiomysql 或 AsyncSession，禁止同步/异步 Session 混用。数据库主导 Endpoint 优先使用普通 `def`；不得在 `async def` 路径直接执行同步数据库 IO。

### 2.2 基础设施镜像

| 服务 | 镜像标签 |
| --- | --- |
| API / Worker | Deployment 阶段从可信 Python 3.12 镜像中冻结明确完整 tag，必要时记录 digest；禁止最终使用浮动 `python:3.12` |
| MySQL | `mysql:8.0.41` |
| Redis | `redis:7.4.2-alpine` |
| RabbitMQ | `rabbitmq:3.13.7-management-alpine` |
| Milvus | `milvusdb/milvus:v2.5.10` |
| etcd（Milvus 依赖） | `quay.io/coreos/etcd:v3.5.16` |
| MinIO（Milvus 依赖） | `minio/minio:RELEASE.2024-12-18T13-15-44Z` |
| Nginx | `nginx:1.27.4-alpine` |

Embedding 模型 **bge-m3** 在 RAG 阶段以独立服务或 Provider 接入，版本在实现 RAG 时锁定模型哈希并追加到本文。阶段 0 不部署 GPU。

### 2.3 AI 生产模型（逻辑名，具体商用名以企业开通为准）

| 能力 | 家族 | 接入 |
| --- | --- | --- |
| LLM | Qwen-family | Production LLMProvider |
| VLM | Qwen-family | Production VisionProvider |
| ASR | 企业选定 ASR | Production ASRProvider |

Development 实现不得调用真实商用端点。

## 3. 环境

| 环境 | 数据 | Provider |
| --- | --- | --- |
| development | 本地 Docker / 空库 | 全部 Development |
| staging | 脱敏样本 | 可接沙箱微信/短信 |
| production | 真实 | Production，密钥由部署注入 |

Windows 本地开发解释器冻结为 **Python 3.12.10**。Day 1 Task 1 只有在 Host 与项目 `.venv` 均报告 Python 3.12.10、metadata 允许该版本、依赖可安装且 pytest/FastAPI 可运行时才能 PASS。`.venv` 必须由当前 Host 解释器通过 `python -m venv .venv` 创建，禁止混用全局与项目包。

Production Docker Runtime 与 Windows Host Runtime 分离，不要求 Patch Version 完全相同。Production 的具体 Python 3.12 Security Patch Version 在 Deployment 阶段依据可信镜像可用性、依赖兼容性、安全状态及完整测试结果单独冻结；交付时必须固定明确 image tag，必要时进一步固定 digest。

Development `DATABASE_URL` 与 Integration Test `TEST_DATABASE_URL` 必须指向不同数据库。测试数据库必须是明确可销毁的 MySQL 8 实例或独立 schema；Migration downgrade、破坏性测试和测试数据清理禁止指向 Development 或 Production 数据库。

## 4. 配置

- 仅环境变量，见仓库根目录 `.env.example`
- Compose 使用 `env_file`，不把密码写进镜像

### 附件扫描 Provider

附件只有完成可信扫描后才可供业务读取。`FILE_SCANNER_PROVIDER=clamav` 使用部署方内网 clamd 的 INSTREAM 协议传输上传内容，不传文件路径；设置 `FILE_SCANNER_HOST`、`FILE_SCANNER_PORT`（默认 3310）、`FILE_SCANNER_TIMEOUT_SECONDS`（默认 10 秒）。扫描大小上限沿用 `UPLOAD_MAX_MB`。仅完整的 `stream: OK` 响应记 `clean`；发现威胁记 `blocked`；超时、断连、超限和未知响应保持 `pending`，禁止当作扫描通过。扫描请求不自动重试。

`FILE_SCANNER_HOST` 必须是部署方指定的私网或回环数字 IP，不接受 DNS 名称、URL、未指定地址或公网地址。连接/发送/接收共用单次总超时；避免操作系统 DNS 解析和多地址重试突破请求时限。容器部署应使用专用网络固定扫描器 IP 或固定内网主机 IP；不能填写仅在宿主机可达的 127.0.0.1。扫描地址变动需要配置变更后重建应用，不能依赖启动时自动解析域名。

clamd TCP 不提供身份认证或加密，只允许同机受限网络或企业受信内网，禁止开放公网。部署方必须配置和维护病毒库更新、访问限制、StreamMaxLength（不低于上传上限）、MaxFileSize/MaxScanSize 与内容扫描限制；不能将 PING 或端口连通视为文件扫描验收。交付前须针对所选真实引擎验证普通文件、官方 EICAR 测试样本与引擎停机时的失败关闭。当前添加 Provider 源码不表示扫描服务已安装或验证。

`unavailable` 仍默认失败关闭；`development` 仅开发使用且保留 `skipped_dev`，生产禁止。启用真实扫描需要部署方准备服务，不由业务 API 拉起或下载引擎/病毒库。

### 附件扫描的三态与就绪报告（D6）

`/readyz` 的 `media_pipeline` 报告本次部署实际生效的扫描形态，三态互斥：

| `scan_mode` | 条件 | `media_scanned` | `media_becomes_usable` | `reason_code` |
| --- | --- | --- | --- | --- |
| `real` | `FILE_SCANNER_PROVIDER=clamav` | `true` | `true` | `null` |
| `demo_stub` | `FILE_SCANNER_PROVIDER=development` | `false` | `false` | `MEDIA_SCAN_STUB` 或 `MEDIA_SCAN_STUB_UNACKNOWLEDGED` |
| `unavailable` | `FILE_SCANNER_PROVIDER=unavailable`（默认） | `false` | `false` | `MEDIA_SCAN_UNAVAILABLE` |

规则：

- 只有真实扫描（`clamav`）才允许 `media_becomes_usable=true`。`development` 桩不检查任何字节，只写 `scanner_status=skipped_dev`，因此媒体「能用但未验证」——就绪报告必须说清楚，不得报成 scanned。
- `development` 在 `development`/`test` 环境是预期形态，只报 `demo_stub`；在 `staging`/`production` 属于配置错误，除非显式声明 `FILE_SCANNER_DEMO_ACKNOWLEDGED=true`，否则 `reason_code=MEDIA_SCAN_STUB_UNACKNOWLEDGED` 且 `/readyz` 的 `status` 降为 `degraded`（HTTP 仍 200，媒体不在存活路径上）。
- 演示环境要留痕：`deploy/.env.delivery` 同时设置 `FILE_SCANNER_PROVIDER=development` 与 `FILE_SCANNER_DEMO_ACKNOWLEDGED=true`，属于显式声明的演示模式；对外取证时必须按 `demo_stub` 表述，不能写成「附件已扫描」。
- 复验命令：`curl -s http://<host>:<port>/api/v1/readyz | jq '.data.media_pipeline'`，期望 `scan_mode`、`media_scanned`、`reason_code` 三项与上表一致。

### 工单日志的重复观测与保留策略（D9）

`work_order_logs` 是审计轨，不自动清理。但「等待派单」是可重复观测的状态而不是业务事件：`sweep_waiting_dispatch` 每个 sweep tick（`SLA_SWEEP_INTERVAL_SECONDS`，默认 30s）都会重新评估每个等待中的工单，过去每次评估都插一行 `AUTO_DISPATCH_WAIT`。交付库实测 105,467 / 106,438 行（26 个工单，40 天），平均间隔 196.8s，`request_id` 全库唯一 ⇒ 原有的「同 request_id 不重复写」完全失效。

写入侧（`DISPATCH_WAIT_LOG_WINDOW_SECONDS`，默认 3600，0 = 恢复旧行为）：

- sweep 路径的 `request_id` 在窗口内固定（`dispatch-wait:<work_order>:<窗口序号>`），同一窗口重复 tick 撞同一个 id，不新增行。
- 同一工单同一 `reason_code` 在窗口内重复观测时，聚合进已有行（日志 `auto_dispatch_wait_aggregated`）；**`reason_code` 变化是状态变化，立刻写一行**（例如 `WAITING_ELIGIBLE` → `WAITING_IDLE`）。
- 任务驱动的分析（intent 用 `request_id` 关联成功）不加聚合，仍按 attempt 落行。

存量历史用 `backend/scripts/ops/prune_work_order_logs.py` 处理，**默认 dry-run**：

```bash
# 只算不删（默认）——打印将删除多少行
python backend/scripts/ops/prune_work_order_logs.py --days 30
# 真删 30 天以前的 AUTO_DISPATCH_WAIT
python backend/scripts/ops/prune_work_order_logs.py --days 30 --apply
# 保留窗口内每个工单每天只留最新一行
python backend/scripts/ops/prune_work_order_logs.py --days 30 --collapse-daily --apply
```

只处理 `--event-code` 指定的重复观测事件（默认仅 `AUTO_DISPATCH_WAIT`）；`CREATE`/`ASSIGN`/`REASSIGN`/`ANALYZE` 等业务事件不在默认清理范围。交付库 30 天 dry-run 结果：26,422 行到期（`--collapse-daily` 合计 105,445 行）。

### 反向代理与按客户端限流（D3 / PA-168）

登录与短信凭据接口有两把限流锁：手机号维度（`SMS_SEND_LIMIT_PER_HOUR`、`SMS_LOGIN_LIMIT_PER_HOUR`）与**客户端 IP 维度**（`SMS_IP_LIMIT_PER_HOUR`，默认 30/小时）。IP 维度只有在拿到真实客户端地址时才有意义。

交付栈是 `nginx → api`：api 的 socket peer 恒为 nginx 容器地址，客户端送来的 `X-Forwarded-For` 属于攻击者可控输入。因此 api 只在**对等地址命中 `TRUSTED_PROXY_CIDRS`** 时才读该头，并从右往左取第一个不属于可信网段的 hop（`deploy/nginx.conf` 用 `$proxy_add_x_forwarded_for` 追加真实地址，因此最右侧才是可信来源）。

**部署时必须设置 `TRUSTED_PROXY_CIDRS`。** `deploy/docker-compose.delivery.yml` 已为 api 提供默认值 `${TRUSTED_PROXY_CIDRS:-172.16.0.0/12}`（覆盖 Docker 默认 bridge/compose 网段）；使用自定义网络时按实际网段收窄：

```bash
docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' property-ai-delivery_default
```

错配后果（两者都严重）：

| 配置 | 现象 |
| --- | --- |
| 未设置 `TRUSTED_PROXY_CIDRS` | 全部用户共用 nginx 那一个 IP 桶：**累计 30 次登录/小时即对整站返回 429**（手机号桶还各自新鲜）；伪造 `X-Forwarded-For` 无法逃离该桶 |
| 设置过宽（如 `0.0.0.0/0`） | 客户端自带 `X-Forwarded-For: <任意地址>` 即被采信，IP 维度可用一行请求头绕过，等于只剩下手机号一把锁 |

实测（2026-11-11，`nginx → api` 同栈拓扑、api 与 nginx 分容器）：未设可信网段时，30 个不同手机号各发一次登录 → 全部 401，第 31 个全新手机号 → `429 AUTH.SMS_RATE_LIMITED`，Redis 键为 `sms:login:ip:172.21.0.14`（nginx 容器地址）；设置 `TRUSTED_PROXY_CIDRS=172.21.0.0/16` 后同一入口按 `X-Forwarded-For` 分桶，键为 `sms:login:ip:203.0.113.7` 与 `sms:login:ip:198.51.100.9`，两个客户端互不影响。

两种维度返回完全相同的 `code`/`message`/空 `details`，调用方无法判断是哪一把锁触发。回归：`backend/tests/integration/test_auth_ip_scope_runtime.py`（HTTP 面：未设可信网段即折叠成单桶、设了之后按客户端分桶、两维度响应逐字节相同）与 `backend/tests/unit/test_pa168_proxy_wiring.py`（锁定 compose 的 `TRUSTED_PROXY_CIDRS` 默认值与 nginx 的 `X-Forwarded-For` 透传）。

运维说明：`SMS_IP_LIMIT_PER_HOUR` 已在 `deploy/docker-compose.delivery.yml` 暴露为 `${SMS_IP_LIMIT_PER_HOUR:-30}`，改值只需改环境后 `docker compose up -d api`（无需重建镜像）；手机号维度仍由 `SMS_SEND_LIMIT_PER_HOUR` / `SMS_LOGIN_LIMIT_PER_HOUR` 控制。调大 IP 预算前先确认 `TRUSTED_PROXY_CIDRS` 已按实际网段收窄，否则等于给整站共用桶放宽额度。

### 本地 AI 适配器的预算与评审证据（PA-166）

适配器（`scripts/ops/local_ai_http.py`）在 `private` 目录里给每次推理调用写一个 `call-NNN/`（`reservation.json` + `result.json`），累计预算按「`call_baseline` + 已计数调用数 < `limits`」判定。评审证据（`call-001` 等被 `resolutions` 复核过的资产）与线上调用**同目录**，重启时若把归档证据拷回 `calls/`，它会被当成新花费，出现「恢复证据即打爆预算」——对外恒为 `MODEL_GUARD_OR_REQUEST_FAILED_NO_RETRY`，`detail_code` 全空。

现在预算只计**未被声明为证据**的调用：

| 配置键 | 作用 |
| --- | --- |
| `evidence_records` | 声明 `calls/` 下属于历史证据的目录名（`["call-001", …]`）。这些目录仍须存在（`work-ai-runtime.ps1 up` 会校验若干条），但**不进入**当前窗口的调用数与时长。`resolutions` 里列出的 `record` 自动计入，无需重复声明 |
| `call_baseline` / `limits` | 语义不变：窗口前已在预算外花掉的调用数 + 本窗口已计数调用数 必须小于 `limits` |

`GET /healthz` 的 `budget` 字段会回显 `limits`、`call_baseline`、`counted`（本窗口计数）、`counted_records`、`evidence_excluded` 与 `evidence_records`，用来判断「还剩多少」和「哪些被当成证据」。每次 `reservation.json` 也会记 `budget_counted: true` 与 `evidence_excluded_before`，事后可复算。

判定不变的部分：完成屏障、失败输入不自动重放、证据缺失/漂移仍然 fail-closed（`gate()` 仍遍历**全部**调用，包含证据）。改配置后需重启适配器才生效；重启与单入口回归由 `deploy/ai-runtime.ps1 up` 负责。

`up` 每次都会重写 `config.json`（`deploy/work-ai-runtime.ps1` 的 `$config` 块是唯一来源），所以手工往里加的键会在下一次 `up` 时消失。`evidence_records` 因此走**清单文件**：`tmp\delivery-work-ai-runtime\evidence-records.txt`，每行一个 `call-NNN`，`up` 读它写进配置（缺文件＝没有证据声明）。改清单同样要 `up` 一次才生效。

### 运行时的单一入口（只允许 `ai-runtime.ps1 up|down`）

`deploy/ai-runtime.ps1 up|down|status|verify|resolve-unknown` 是 Ollama 与工单适配器的唯一入口。**禁止手工起适配器**（`python scripts/ops/local_ai_http.py serve --config …`）：手工进程不会被 receipt 记录，`ai-runtime.ps1 status` 会继续显示旧 receipt 的 `launcher_pid` / `listener_pid`，`config_sha256`、`runtime_binding_sha256` 与实际运行的东西脱钩，出问题时无法判断「跑的是哪份代码」。

对账三步（每次收口后都做）：

1. `deploy\ai-runtime.ps1 status` 的 `receipt.launcher_pid` / `receipt.listener_pid` 必须与 `netstat -ano | findstr :11437` 的 pid、以及 `python.exe … local_ai_http.py serve` 的父子进程一致；
2. `receipt.source_commit` 必须等于当前要跑的提交（它只对 `equiv_scope` 有效，仓库 HEAD 用 `git rev-parse HEAD` 看），`receipt.adapter_sha256` 等于 `git show <commit>:scripts/ops/local_ai_http.py` 的 sha256；
3. `GET /healthz`（带 `internal-token.txt` 里的 Bearer）返回 `status=ready`、`reservation_gate=READY`、`accepting_requests=true`，且 `GET http://127.0.0.1:8093/api/v1/readyz` 的 `ai_credentials.status=configured`。

改了 `scripts/ops/local_ai_http.py` 之后，`up` 会因为钉住的 `$ExpectedAdapterSha256` 对不上而**拒绝启动**——这是设计使然：必须先在 `deploy/work-ai-runtime.ps1` 里同时更新 `$SourceCommit` 与 `$ExpectedAdapterSha256`（按 `git show <commit>:<path>` 的 LF 字节算 sha256，与 `Get-FileHash` 口径一致），再 `up`。不要绕过校验直接手起适配器。

### 资源地板：可观测、可覆盖（PA-130）

适配器在每次 Qwen 调用前后比较宿主剩余内存与显卡空闲显存，低于地板即拒绝（`QWEN_RESOURCE_GUARD` → 对外 `RESOURCE_ADMISSION_REJECTED`）。地板过去是三个写死的字面量，只能报「拒了」；现在它们可观测、可覆盖，默认值不变：

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `LOCAL_AI_RAM_FLOOR_MIB` | 4500 | 请求期间要求的最小可用内存（MiB） |
| `LOCAL_AI_GPU_FLOOR_MIB` | 2000 | 模型常驻时要求的最小空闲显存（MiB） |
| `LOCAL_AI_COLD_START_GPU_FLOOR_MIB` | 6000 | 模型**未常驻**、本次请求要现场加载时要求的最小空闲显存（MiB） |

规则：

- 非法值（非整数、`≤0`、`>262144`）在**进程启动时**就抛 `ValueError: INVALID_FLOOR_MIB:<变量名>:<原值>`，不会带着没选过的地板接请求；空值等同未设置，用默认值。
- `GET /healthz` 与 `GET /readyz`（同一载荷）新增两块：`floors` 回显三者生效值与来源（`default` / `env:<变量名>`），`resource_floor` 给出 `floor_mib` / `observed_mib` / `short_by_mib`（正数＝还差多少）以及 `ram` / `gpu` 分项与 `observation_error`（`nvidia-smi` 不可用时不会把就绪探针变成 500）。
- 资源拒绝时，`POST` 的 503 体与 `pre-reservation-failures.jsonl` 记录都会带上同一份 `resource_floor`；错误串与非资源类状态码不变，`detail_code` 语义不变（仍只回显白名单内的原因，`QWEN_RESOURCE_GUARD` 不在名单里，仍为 `null`）。
- 启动 banner 会多打一行 `resource floors: {...}`，写明三个生效值与可覆盖的变量名。

**32GB / 16GB 机器的现实**：27B（q3）常驻时实测空闲显存 1941–1962 MiB、宿主可用内存 4417–4777 MiB，而默认地板是 2000 / 4500，也就是说**每次加载都会贴地板、差几十 MiB**。这时报 `short_by_mib: 38~59` 属于**容量问题**，不是代码回归：机器在 27B + 视觉这条路上本来就没有余量。可选路径（按代价从小到大，均需另行评审，本文只记录）：

1. **降上下文**：把 `PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL` 指向更小 ctx 的已批准变体（如 `…-g52-ctx8192`），减少 KV cache 占用；
2. **只跑文本**：关闭图片/视觉调用，省掉 VLM 那一轮常驻与显存峰值；
3. **换小模型**：改用更小的已批准模型，重跑能力回归后替换——这会改变对外承诺的模型口径，必须走评审，不能由运维直接改。

覆盖示例（仅用于临时排查，需在部署记录里写明原因与时长）：

```powershell
$env:LOCAL_AI_RAM_FLOOR_MIB      = "4450"
$env:LOCAL_AI_COLD_START_GPU_FLOOR_MIB = "1950"
deploy\ai-runtime.ps1 up
```

覆盖后必须能从 `GET /readyz` 的 `floors.source` 看出「是 env 覆盖，不是默认值」；排查结束后恢复默认并重启，否则这台机器上的地板就再也没人校验过。

### 媒体模型版本与成功检查点（2026-11-02）

`VLM_MODEL_REVISION`、`ASR_MODEL_REVISION` 可填写已核实的权重 SHA-256（例如 `sha256:<64位摘要>`）或不可变部署发布号。标签最长128字符，仅允许字母、数字、点、下划线、冒号、加号、连字符；禁止凭证或业务内容。实际请求仍使用 `VLM_MODEL` / `ASR_MODEL`，revision 仅进入成功检查点指纹。

同一别名更换权重时同步更改 revision：旧单文件及聚合媒体检查点不再匹配。空值兼容原有指纹，**不能**防止服务端未声明的同名替换。客户端标签不能证明服务端实际制品，部署记录仍须核对模型文件/镜像摘要与服务启动参数。

切换前只读盘点仍在重试或等待的媒体任务、旧指纹数量与预计新增推理预算，保留旧部署可回退依据；本配置不自动重放已完成工单或失败队列。LLM完整分析复用与RAG知识索引版本属于各自契约，不能用本节的媒体指纹宣称它们也已覆盖。

## 5. 迁移与备份

### 甲方全新空库安装契约

甲方初始化使用单独 `app.cli.customer_install` 入口：`plan` 离线生成不含密码的目标、环境、固定 head 和源码摘要；`apply` 必须提供原清单及操作者核对后的 SHA-256。清单变化、源码变化、目标变化、初始化身份缺失均在连接前拒绝。实际连接后先获取该库安装互斥锁，并确认没有表/视图、事件、触发器或存储过程，才允许按既有迁移执行；只接受 staging/production 的新库，不接受本项目已有开发、验收库及 MySQL 系统库。

此流程不创建数据库、不自动清库、不覆盖既有数据、不做 stamp/create_all。使用同一物理连接执行迁移及初始管理员事务；历史迁移只新增该受控连接内的安装许可分支，原测试/本地交付授权及结构前置检查保持有效，迁移 DDL 不变。失败后保留现场；MySQL DDL 不能保证整体回滚，禁止盲目重跑或自动删除。数据库安装不代表存储、模型、微信、短信或扫描服务已就绪，后续配置与验收独立记录。实际执行持久库安装仍需部署负责人批准。

- 发布流程：备份 → `alembic upgrade head` → 启动 API/Worker → 健康检查
- 应用与 Alembic 统一使用 PyMySQL；所有 Schema 变化必须通过历史可追踪的 Alembic Migration
- 禁止 `create_all` 代替 Migration，禁止删除历史 Migration 解决冲突
- MySQL：每日 `mysqldump` 或 xtrabackup（生产选定一种写入运行手册）+ binlog 至少 7 天
- 文件存储：对象存储版本或每日同步
- 恢复：在 staging 用备份演练，阶段 1 只要求文档化，Day 1 不做真实恢复演练

## 6. Git

仓库根目录为 Git 项目根。当前开发机若未安装 Git，负责人需先安装后再 `git init`。不要把 `.env`、数据卷、真实备份提交进库。
