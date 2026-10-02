# 部署

Production V1 使用 Docker Compose + Nginx。镜像版本见 `docs/09-DEPLOYMENT.md`。运行手册见 `docs/11-OPERATIONS.md`。

## Development 数据面

`deploy/docker-compose.yml` 项目名 `property-ai`，MySQL 卷 `mysql_data`。

```bash
docker compose -f deploy/docker-compose.yml --profile test up -d mysql mysql-test redis rabbitmq etcd minio milvus
```

`docker compose down` **不要**加 `-v`，否则会删除 development 数据。

应用 profile（会使用当前 `.env` 的 `CELERY_TASK_QUEUE`，默认 `property_ai.development`）：

```bash
docker compose -f deploy/docker-compose.yml --profile app up -d --build
```

发布前必须显式：

```bash
cd backend
alembic upgrade head
```

不要在 API 启动时自动 migrate。不要把 Secret bake 进镜像。生产禁止 `dev_seed`。

## Production-like 排练（可销毁）

独立项目 `property-ai-prodlike`，端口与卷均与 development 隔离：

```bash
docker compose -f deploy/docker-compose.prodlike.yml up -d --build
```

空库迁移目标：`property_ai_staging` @ `127.0.0.1:3316`，期望 revision `20260917_0007`。

Nginx：`http://127.0.0.1:8081`（`/api/`、`/healthz`、`/readyz`）。

## Day 4 一次性跨进程验证（可销毁）

独立项目 `property-ai-day4`，Nginx `http://127.0.0.1:8082`。使用 `APP_ENV=test`、独立库 `property_ai_day4`、vhost `property_ai_day4`、队列 `property_ai.day4`、collection `knowledge_chunks_day4`、bucket `property-ai-day4`。允许标注的 Fake Provider。禁止在 staging/production 打开 DevAuth。

```bash
python scripts/ops/day4_prepare.py
docker compose -f deploy/docker-compose.day4.yml up -d
# 如需重建镜像：docker compose -f deploy/docker-compose.yml --profile app build
DAY4_BASE_URL=http://127.0.0.1:8082 TEST_DATABASE_URL=mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_day4?charset=utf8mb4 python scripts/ops/day4_e2e.py
```

`down` 只允许针对 `deploy/docker-compose.day4.yml`，不要带 development 项目的 `-v`。

TLS：`deploy/nginx-tls.conf.example`。无真实证书时：

```
HTTPS CONFIGURATION = READY
LIVE TLS = BLOCKED_BY_DOMAIN_CERT
```

## Delivery candidate（持久化、可重复启动）

`docker-compose.delivery.yml` 是单机交付制品，不依赖开发机绝对路径、旧容器 ID、PID 或历史数据库快照。它使用独立 Compose 项目和具名持久卷；API、Worker、Beat 不会自动执行迁移。

### 当前本地接收版本（2026-10-02）

- 产品/运行源码：`3d7c6df7e5549d2790a095e785f70c0754fb3330`；WO19 三附件限定验收与证据提交：`9f2f16dc7cf19a0f3f155fff27446910c7a4cea5`。
- API、Worker、publisher 与 Beat：同一镜像 `property-ai-delivery:3d7c6df`（`sha256:a2a9f4f96624c918d7b2be50e8d8f56d539d27e9e91a410c3d0063f644e5d8f1`），本地入口 `http://127.0.0.1:8093`。2026-10-02 接收核对时四个组件均运行，API healthy，`healthz`/`readyz` 正常。
- 工单 AI：`11437 -> 11435`，Qwen3.8 27B Q3_K_M，context 8192；当前适配器源码 `61b762d9775845c47cf0b43f3003a3f4d1cb5519`，范围 `work_order_only`，状态核对为 READY/idle。
- 知识 AI：`11436 -> 11438`，Qwen3.5 4B，context 4096，配合 bge-m3、bge-reranker-v2-m3 与 `knowledge_chunks_delivery_real_1024`。真实 RAG 的版本绑定验收保留；接收核对时 11438 的旧收据指向已退出进程，因此实际使用前必须按下方顺序重新启动并以两个 status 命令为准。本次遵守“不重启”边界，没有为文档整理启动或推理。
- WO19 已完成一张图片和两段录音的理解与合并分析，但因当前小区没有具备 CLEANING 职责的合法在岗人员而处于 `WAITING_ELIGIBLE`；这是合法等待，不得人工改职责或派单来制造 CLOSED。

正常本地成品入口始终是本节的 `delivery.ps1` 与 8093 环境；它不依赖 Codex 常驻、逐单测试 ID 或 3317 验收数据。`scripts/ops/serve_auth001_retry.py` 仅用于已批准的 3317 隔离验收：测试 Proof、注入时钟和精确工单 ID Beat 均受目标守卫约束，且该入口把 API/Worker/Beat/投递器的所有消息统一路由到 `property_ai.auth001.persist.v2.sla.acceptance`，不得连接或消费导入快照携带的原队列。该验收入口不是部署或生产启动方式。

Windows 本地验证：

```powershell
Copy-Item deploy/.env.delivery.example deploy/.env.delivery
Copy-Item deploy/.env.delivery.bootstrap.example deploy/.env.delivery.bootstrap
# 仅在本机编辑两份文件并替换占位值；它们均被 Git 忽略。
./deploy/delivery.ps1 config
./deploy/delivery.ps1 init   # 仅用于全新环境：基础设施、迁移、存储初始化和应用
./deploy/delivery.ps1 up     # 已初始化环境的日常启动；不重新迁移
./deploy/delivery.ps1 status
./deploy/delivery.ps1 backup
./deploy/delivery.ps1 diagnose
./deploy/delivery.ps1 ollama-start # 启动固定 loopback 11435 模型服务；健康检查不加载模型
./deploy/delivery.ps1 ollama-status
./deploy/delivery.ps1 work-ai-start  # 启动冻结的工单/图片/语音适配器；健康检查不推理
./deploy/delivery.ps1 work-ai-status
./deploy/delivery.ps1 rag-ollama-start # 启动独立 loopback 11438/4096 RAG 模型服务
./deploy/delivery.ps1 rag-ollama-status
./deploy/delivery.ps1 rag-start   # 启动 11436 RAG-only 适配器，绑定 11438；健康检查不推理
./deploy/delivery.ps1 rag-status
./deploy/delivery.ps1 rag-stop    # 请求执行中会拒绝停止；保留模型、账本和私密令牌
./deploy/delivery.ps1 rag-ollama-stop
./deploy/delivery.ps1 work-ai-stop   # 精确停止适配器；不停止模型服务
./deploy/delivery.ps1 ollama-stop  # 仅停止该入口记录的精确实例；保留权重和日志
./deploy/delivery.ps1 stop   # 保留全部具名卷
```

本机私密账号不进入 Git：初始化 SYSTEM_ADMIN 只存于忽略的 `deploy/.env.delivery.bootstrap`；当前隔离演示账号由授权 fixture 生成后存于忽略的 `tmp/delivery-auto-8093/fixture-context.json`，只能在本机合成环境使用，不得复制到报告、日志或部署镜像。部署到企业主机时应改由部署方 secret manager 保存，不得依赖这个 `tmp` 文件。正常操作从小程序密码/短信登录开始：管理员在首页进入“账号合并 / 高风险恢复”“身份恢复审核”或“SLA 策略管理”，住户从“提交报修”选择已授权房屋并确认本次联系电话；测试 Proof 只允许 3317 隔离入口，8093 正常配置不得开启该开关。

小程序唯一源码入口是 `D:\property-ai\miniprogram`。在微信开发者工具中使用本机 `project.config.json` 的 AppID 打开该目录；私有 `config/env.local.js` 的 Development 地址应为 `http://127.0.0.1:8093/api/v1`。该地址只适用于本机模拟器：手机不能把自己的 `127.0.0.1` 当作电脑。既有真机媒体验收使用的是一次性、仅同网本人可访问且已经撤销的临时入口；再次做真机操作必须重新建立受控入口，不能开放公网、数据库、队列或模型管理端口。原生选图、录音、上传、预览和三附件理解的既有验收无需重复。

正常新工单由业务提交同事务保存任务意图，再由 publisher/Worker 处理，不要求 Codex 常驻，也不要求逐单加入测试 ID 白名单。受保护历史失败、特定恢复 generation 和模型诊断使用追加式账本与显式授权；这些记录是安全/证据边界，不能清空，也不能当作正常业务吞吐配额。正常运行仍受 Provider 请求预算、单模型单并发、共享资源锁、超时、RAM/VRAM floor、幂等和持久任务恢复约束；测试账本用尽不等于可以绕过运行配额，运行配额也不能改写历史测试结论。

交付包从已提交源码构建，不包含 `deploy/.env.delivery`、`deploy/.env.delivery.bootstrap`、`tmp/delivery-auto-8093/fixture-context.json`、`miniprogram/config/env.local.js`、截图中的敏感值、未提交 WIP 或模型权重。模型资产由部署方在私密配置中指向已校验目录；不得把重复大权重烘焙进应用镜像。

`init` 的固定顺序是：基础设施健康 → 单独执行 `alembic upgrade head` → 精确守卫的一次性对象存储 bucket 初始化 → API/Worker/持久任务投递器/Beat/Nginx。再次启动用 `up`；`up` 也会幂等核对并确保同一个 delivery bucket 存在。不得用 `down -v`。`backup [文件名.sql]` 只在 `deploy/backups/` 内新建不覆盖的 SQL 和 SHA-256 清单；省略文件名时使用 UTC 时间与随机后缀。显式同名并发请求通过原子预约互斥，SQL 与清单先写各自的唯一临时文件，再以不覆盖的文件移动发布。`restore-check <dump>` 只接受该目录内与清单哈希、字节数和源库名一致且不含跨库/全局语句的备份，并以仅获目标库权限的临时账号导入全新的 `<MYSQL_DATABASE>_restore_check`。本次受控验收可显式传入第三参数且只接受 `property_ai_delivery_check_restore_check_029`；其他显式库名拒绝。目标已存在时拒绝覆盖，应用不会连接恢复库。

实际迁移分为受控的 0012 前置、一次性 SYSTEM_ADMIN 初始化、`alembic_version` 兼容扩宽和 0014。管理员手机号/密码只来自 `.env.delivery.bootstrap`，仅 migrate 服务读取，日志不输出原值；成功后应移入部署方 secret manager 或从主机文件中安全移除。历史迁移的业务 DDL 不变，新增的 delivery guard 只接受 `staging + mysql:3306/property_ai_delivery_check + 显式目标授权`。

Linux 服务器使用相同 Compose 文件和 `.env.delivery`，命令等价为：

```bash
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml config --quiet
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml up -d mysql redis rabbitmq etcd minio milvus
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml --profile tools run --rm migrate
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml up -d api worker publisher beat nginx
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml ps
```

本地 clean-room 排练可以保留 Development 微信/短信/AI Provider。使用真实 MinIO 但尚未接企业文件扫描服务时，必须在本机私密配置中显式设置 `FILE_SCANNER_PROVIDER=development`，保存的状态为 `skipped_dev`，不能写成经过恶意文件扫描；模板默认 `unavailable`，上传内容保持 `pending` 并失败关闭。Production 禁止 Development 文件扫描器。正式激活必须在私密配置中切换到企业通道及自托管 Provider；未配置可信身份 Provider 时，身份接管继续失败关闭。`EMBEDDING_DIMENSIONS=8` 只用于 Development 排练；真实 bge-m3 使用新的 1024 维集合，禁止复用 8 维集合。

知识问答与工单分析具有独立的模型发布门槛。`RAG_LLM_PROVIDER=inherit` 保持旧配置；只有知识问答已经通过而工单分析仍未达标时，可在私密配置中单独设置 `RAG_LLM_PROVIDER=production`、`RAG_LLM_BASE_URL`、`RAG_LLM_MODEL` 和 `RAG_LLM_API_KEY`，同时继续保持 `LLM_PROVIDER=development`。真实知识接线还必须成组设置 1024 维 bge-m3、真实 reranker、`MILVUS_METRIC_TYPE=COSINE`、`EMBEDDING_NORMALIZATION=l2` 以及全新的隔离集合名。该隔离不会授权真实工单 AUTO；两条能力必须分别验收。

Windows 本地 Ollama 服务由 `delivery.ps1 ollama-start|ollama-status|ollama-stop` 管理。它只绑定 loopback 11435，固定已审可执行文件哈希、项目模型目录、单模型单并发、no-cloud 和显存保留配置；启动与状态检查不执行推理。可用 `PROPERTY_AI_OLLAMA_EXE` 覆盖可执行文件位置，但文件哈希仍必须匹配。停止操作只接受当前收据绑定的 PID、启动时间、路径和监听归属，不按进程名批量停止。

停止顺序按各自链路执行：RAG 为 `rag-stop` 后 `rag-ollama-stop`，工单 AI 为 `work-ai-stop` 后 `ollama-stop`。适配器先拒绝新请求并确认没有活动请求，模型服务还要求对应模型已从显存卸载，再终止收据绑定的完整进程树。若对应 adapter 仍在监听、模型仍驻留或任一退出状态无法确认，停止操作失败关闭并保留当前收据。每次新启动使用排他预约、原子收据和唯一日志文件；旧日志及停止收据不会被下一次启动覆盖。

Windows 本地 RAG 模型服务由 `delivery.ps1 rag-ollama-start|rag-ollama-status|rag-ollama-stop` 管理，只绑定 loopback 11438，并固定 4096 context。RAG 适配器由 `delivery.ps1 rag-start|rag-status|rag-stop` 管理；应先启动 11438，适配器只绑定 loopback 11436，自动生成并保留 Git 忽略的内部令牌，使用追加式调用账本，并拒绝工单分析协议。11435/11437 与 11438/11436 共享本机模型资源锁，避免两个模型同时加载越过资源 floor。Compose 通过 `host.docker.internal` 访问适配器端口；Linux 部署应由部署方把同名私密令牌注入 `DELIVERY_RAG_INTERNAL_TOKEN`，并把私有模型服务地址写入 `.env.delivery`，不得把令牌提交到 Git。

本地 CPU 首次加载 bge-m3 可能明显慢于后续请求。交付配置模板为 embedding/reranker 分别预留 45 秒和 15 秒提供方超时；仍应保留适配器自身的资源、调用次数和阶段时限保护，不能用无限 HTTP 超时替代它们。若部署硬件或模型资产变化，应基于该环境实测重新收敛这两个值。
