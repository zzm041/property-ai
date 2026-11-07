# 部署

Production V1 使用 Docker Compose + Nginx。镜像版本见 `docs/09-DEPLOYMENT.md`。运行手册见 `docs/11-OPERATIONS.md`。

私有自托管模型的新配置、离线文件校验和 HTTP 入口见 [selfhost/README.md](selfhost/README.md)。它不依赖历史验收报告；已完成源码控制检查、现有资产离线核验，以及当前环境中文字、图片、语音、向量、重排各一次固定合成输入的真实调用。新环境安装与完整客户部署仍待验证。

甲方全新数据库安装入口见 [CUSTOMER-INSTALL.md](CUSTOMER-INSTALL.md)。它使用独立客户配置和具名空库授权；下文 delivery.ps1、历史模型启动器与受控 staging 排练不能作为甲方任意环境的一键安装流程。客户空库迁移已通过一次临时 MySQL 验证；模型可移植安装、真实扫描引擎及整套客户部署仍待交付收尾。

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

空库迁移目标：`property_ai_staging` @ `127.0.0.1:3316`。先运行 `alembic heads` 核对交付版本，迁移后 `alembic current` 应一致；当前源码 head 为 `20261003_0015_work_order_media_cycle_links`，不能以旧阶段的 `20260917_0007` 判断完整部署成功。

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

### 历史本地接收快照（2026-10-03）

本节版本、健康状态、队列和模型状态均为当日快照，不是当前运行声明。后续 2026-11-04 本地交付记录见 `reports/delivery-closeout-20261103/FINAL-ENGINEERING-ACCEPTANCE.md`；运行时以实际制品、配置和现场检查为准。

- 后端与小程序本地候选：`95375147cf0392a1cbd8a84cf91bc72a54950507`，均来自已提交源码。应用镜像是在保留的 `property-ai-delivery:ede660f` 依赖层上用该提交的纯 Git 归档离线覆盖构建，没有同步或改变依赖；预存 `backend/app/model/ai.py`、`miniprogram/project.config.json` 和其他未提交 WIP 未进入镜像。
- API、Worker、publisher 与 Beat：同一镜像 `property-ai-delivery:9537514`（`sha256:347cc917f747eb2e84fc75fa0b4f493aad96a7bcd76c39f9307a88de2ace6283`），本地入口 `http://127.0.0.1:8093`。四个组件均绑定上述提交并运行，API healthy，`healthz`/`readyz` 正常；数据库为 `property_ai_delivery_check@20261003_0015_work_order_media_cycle_links`，专属业务队列为 `0 ready / 0 unacknowledged / 1 consumer`，历史 DLQ 未读取、重放或清除。
- 工单 AI：`11437 -> 11435`，Qwen3.8 27B Q3_K_M，context 8192；当前适配器源码 `61b762d9775845c47cf0b43f3003a3f4d1cb5519`，范围 `work_order_only`，状态核对为 READY/idle。
- 知识 AI：`11436 -> 11438`，Qwen3.5 4B，context 4096，配合 bge-m3、bge-reranker-v2-m3 与 `knowledge_chunks_delivery_real_1024`。旧退出收据已通过不可变精确绑定解决；当前 11438 为 Ollama `0.34.4`、no-cloud、单模型单并发且无驻留模型，11436 为 READY/idle。启动和状态核对未执行推理；历史真实 RAG PASS 仍按原版本保存，不能把“当前在线”写成一次新的问答验收。
- WO19 已完成一张图片和两段录音的理解与合并分析，但因当前小区没有具备 CLEANING 职责的合法在岗人员而处于 `WAITING_ELIGIBLE`；这是合法等待，不得人工改职责或派单来制造 CLOSED。

当前 V1 本地边界按正式规格而不是旧矩阵文字判断：管理员的 Property/Tenant 当前运营快照已经实现；`docs/BACKLOG.md` 明确把 SLA 报表导出放在 V1.1，不能为了“收口”临时发明历史区间、分母、文件格式或存储语义。迁移 `20261003_0015_work_order_media_cycle_links` 已在全新演练库完成 0014→0015 验证后应用到 3327 delivery 数据库：`work_order_media.process_id/receipt_id` 保持 nullable，旧记录不回填，新的 ON_SITE 与 BEFORE/AFTER 分别按处理周期和回执作用域绑定。复合外键拒绝错周期/错作用域关联；REPORT 和历史不明确记录继续保持未绑定，禁止靠时间戳或审计 JSON 猜测归属。

本地 Development 微信路径在当前候选上已经完成页面闭环：住户通过普通密码会话绑定当前开发设备，退出后再次执行小程序 `wx.login` 回到同一用户、租户和小区。该实现只在 `develop` 版本使用稳定的合成设备标识；体验版/正式版继续向 Production Provider 发送原始临时登录码。真实 `jscode2session`、正式凭据和目标域名仍是上线前条件，不能由 Development 结果替代。

正常本地成品入口始终是本节的 `delivery.ps1` 与 8093 环境；它由业务服务持续处理任务，不依赖逐单测试 ID 或 3317 验收数据。`scripts/ops/serve_auth001_retry.py` 仅用于已批准的 3317 隔离验收：测试 Proof、注入时钟和精确工单 ID Beat 均受目标守卫约束，且该入口把 API/Worker/Beat/投递器的所有消息统一路由到 `property_ai.auth001.persist.v2.sla.acceptance`，不得连接或消费导入快照携带的原队列。该验收入口不是部署或生产启动方式。

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
# 已有 0014 安装升级到 0015：先 backup，再执行固定 revision；不要笼统 upgrade head。
./deploy/delivery.ps1 media-cycle-migrate property_ai_delivery_check
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

本机私密账号不进入 Git：初始化 SYSTEM_ADMIN 只存于忽略的 `deploy/.env.delivery.bootstrap`；当前隔离演示账号由授权 fixture 生成后存于忽略的 `tmp/delivery-auto-8093/fixture-context.json` 或 `tmp/media-cycle-0015/fixture-context.json`，只能在本机合成环境使用，不得复制到报告、日志或部署镜像。部署到企业主机时应改由部署方 secret manager 保存，不得依赖这些 `tmp` 文件。正常操作从小程序密码/短信登录开始：管理员在首页进入“账号合并 / 高风险恢复”“身份恢复审核”或“SLA 策略管理”，住户从“提交报修”选择已授权房屋并确认本次联系电话；测试 Proof 只允许 3317 隔离入口，8093 正常配置不得开启该开关。

小程序唯一源码入口是 `D:\property-ai\miniprogram`。在微信开发者工具中使用本机 `project.config.json` 的 AppID 打开该目录；私有 `config/env.local.js` 的 Development 地址应为 `http://127.0.0.1:8093/api/v1`。该地址只适用于本机模拟器：手机不能把自己的 `127.0.0.1` 当作电脑。真机验收只能临时改为绑定本机 WLAN 地址、限制到负责人手机的受控入口，结束后恢复 loopback 并撤销入口；禁止开放公网、数据库、队列或模型管理端口。既有原生报修选图、录音、上传及三附件理解无需重复；补充资料和回执选图的缩略图、提交前预览、成功后分组清理及 0015 周期展示包含在当前候选中，是否完成真机提交以当前交接证据为准。

正常新工单由业务提交同事务保存任务意图，再由 publisher/Worker 处理，无需人工逐单触发，也不要求逐单加入测试 ID 白名单。受保护历史失败、特定恢复 generation 和模型诊断使用追加式账本与显式授权；这些记录是安全/证据边界，不能清空，也不能当作正常业务吞吐配额。正常运行仍受 Provider 请求预算、单模型单并发、共享资源锁、超时、RAM/VRAM floor、幂等和持久任务恢复约束；测试账本用尽不等于可以绕过运行配额，运行配额也不能改写历史测试结论。

交付包从已提交源码构建，不包含 `deploy/.env.delivery`、`deploy/.env.delivery.bootstrap`、`tmp/delivery-auto-8093/fixture-context.json`、`miniprogram/config/env.local.js`、截图中的敏感值、未提交 WIP 或模型权重。模型资产由部署方在私密配置中指向已校验目录；不得把重复大权重烘焙进应用镜像。

`init` 的固定顺序是：基础设施健康 → 单独执行固定 revision `20261003_0015_work_order_media_cycle_links` → 精确守卫的一次性对象存储 bucket 初始化 → API/Worker/持久任务投递器/Beat/Nginx。再次启动用 `up`；`up` 也会幂等核对并确保同一个 delivery bucket 存在。不得用 `down -v`。`backup [文件名.sql]` 只在 `deploy/backups/` 内新建不覆盖的 SQL 和 SHA-256 清单；省略文件名时使用 UTC 时间与随机后缀。显式同名并发请求通过原子预约互斥，SQL 与清单先写各自的唯一临时文件，再以不覆盖的文件移动发布。`restore-check <dump>` 只接受该目录内与清单哈希、字节数和源库名一致且不含跨库/全局语句的备份，并以仅获目标库权限的临时账号导入全新的 `<MYSQL_DATABASE>_restore_check`。受控媒体周期演练可显式使用且只使用 `property_ai_delivery_media_cycle_check`；目标已存在时拒绝覆盖，应用不会连接恢复库。

实际迁移分为受控的 0012 前置、一次性 SYSTEM_ADMIN 初始化、`alembic_version` 兼容扩宽、0014 和固定 0015。管理员手机号/密码只来自 `.env.delivery.bootstrap`，仅 migrate 服务读取，日志不输出原值；成功后应移入部署方 secret manager 或从主机文件中安全移除。历史迁移的业务 DDL 不变；0015 delivery guard 只接受 `staging + mysql:3306/property_ai_delivery_check + 显式正式目标授权`，或一次性精确演练库 `property_ai_delivery_media_cycle_check + drill 授权`。演练账号仅获该库权限并在 `finally` 中无条件删除。

初始化凭证只由 `docker-compose.bootstrap.yml` 叠加文件注入 migrate；PowerShell 的 `init` / `media-cycle-migrate` 自动加载并检查该文件，日常 `up` / `config` 不加载它。Linux 初始化必须显式包含该叠加文件；凭证缺失时 Compose 在启动迁移前拒绝执行。

Linux 服务器使用相同 Compose 文件和 `.env.delivery`，当前受控 staging 排练命令等价为（并非任意生产库安装入口）：

```bash
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml config --quiet
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml up -d mysql redis rabbitmq etcd minio milvus
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml -f deploy/docker-compose.bootstrap.yml --profile tools run --rm \
  -e PROPERTY_AI_MEDIA_CYCLE_MIGRATION=approved \
  -e PROPERTY_AI_APPROVE_DELIVERY_TARGET=mysql:3306/property_ai_delivery_check migrate
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml --profile tools run --rm storage-init
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml up -d api worker publisher beat nginx
docker compose --env-file deploy/.env.delivery -f deploy/docker-compose.delivery.yml ps
```

本地 clean-room 排练可以保留 Development 微信/短信/AI Provider。使用真实 MinIO 但尚未接企业文件扫描服务时，必须在本机私密配置中显式设置 `FILE_SCANNER_PROVIDER=development`，保存的状态为 `skipped_dev`，不能写成经过恶意文件扫描；模板默认 `unavailable`，上传内容保持 `pending` 并失败关闭。Production 禁止 Development 文件扫描器。正式激活必须在私密配置中切换到企业通道及自托管 Provider；未配置可信身份 Provider 时，身份接管继续失败关闭。`EMBEDDING_DIMENSIONS=8` 只用于 Development 排练；真实 bge-m3 使用新的 1024 维集合，禁止复用 8 维集合。

知识问答与工单分析具有独立的模型发布门槛。`RAG_LLM_PROVIDER=inherit` 保持旧配置；只有知识问答已经通过而工单分析仍未达标时，可在私密配置中单独设置 `RAG_LLM_PROVIDER=production`、`RAG_LLM_BASE_URL`、`RAG_LLM_MODEL` 和 `RAG_LLM_API_KEY`，同时继续保持 `LLM_PROVIDER=development`。真实知识接线还必须成组设置 1024 维 bge-m3、真实 reranker、`MILVUS_METRIC_TYPE=COSINE`、`EMBEDDING_NORMALIZATION=l2` 以及全新的隔离集合名。该隔离不会授权真实工单 AUTO；两条能力必须分别验收。

Windows 本地 Ollama 服务由 `delivery.ps1 ollama-start|ollama-status|ollama-stop` 管理。它只绑定 loopback 11435，固定已审可执行文件哈希、项目模型目录、单模型单并发、no-cloud 和显存保留配置；启动与状态检查不执行推理。可用 `PROPERTY_AI_OLLAMA_EXE` 覆盖可执行文件位置，但文件哈希仍必须匹配。停止操作只接受当前收据绑定的 PID、启动时间、路径和监听归属，不按进程名批量停止。

停止顺序按各自链路执行：RAG 为 `rag-stop` 后 `rag-ollama-stop`，工单 AI 为 `work-ai-stop` 后 `ollama-stop`。适配器先拒绝新请求并确认没有活动请求，模型服务还要求对应模型已从显存卸载，再终止收据绑定的完整进程树。若对应 adapter 仍在监听、模型仍驻留或任一退出状态无法确认，停止操作失败关闭并保留当前收据。每次新启动使用排他预约、原子收据和唯一日志文件；旧日志及停止收据不会被下一次启动覆盖。

Windows 本地 RAG 模型服务由 `delivery.ps1 rag-ollama-start|rag-ollama-status|rag-ollama-stop` 管理，只绑定 loopback 11438，并固定 4096 context。RAG 适配器由 `delivery.ps1 rag-start|rag-status|rag-stop` 管理；应先启动 11438，适配器只绑定 loopback 11436，自动生成并保留 Git 忽略的内部令牌，使用追加式调用账本，并拒绝工单分析协议。11435/11437 与 11438/11436 共享本机模型资源锁，避免两个模型同时加载越过资源 floor。Compose 通过 `host.docker.internal` 访问适配器端口；Linux 部署应由部署方把同名私密令牌注入 `DELIVERY_RAG_INTERNAL_TOKEN`，并把私有模型服务地址写入 `.env.delivery`，不得把令牌提交到 Git。

本地 CPU 首次加载 bge-m3 可能明显慢于后续请求。交付配置模板为 embedding/reranker 分别预留 45 秒和 15 秒提供方超时；仍应保留适配器自身的资源、调用次数和阶段时限保护，不能用无限 HTTP 超时替代它们。若部署硬件或模型资产变化，应基于该环境实测重新收敛这两个值。
