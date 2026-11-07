# 11 — Operations Runbook（Production V1 / Day 3）

本文让另一位工程师在不依赖作者脑内知识的情况下完成：配置、启动、迁移、验证、备份、恢复、重启、排障。

Day 3 不引入 Prometheus/Grafana。监控基线见第 12 节。

**禁止** `docker compose down -v` 针对 `deploy/docker-compose.yml`（development）。那会销毁 `mysql_data`。

**禁止** API / Worker 启动时自动 `alembic upgrade` 或 `dev_seed`。

---

## 1. 环境隔离

| 环境 | APP_ENV | MySQL | Redis DB | Celery queue | Milvus collection | MinIO bucket |
| --- | --- | --- | --- | --- | --- | --- |
| development | `development` | `property_ai` :3306 | `/0` | `property_ai.development` | `knowledge_chunks_v1` | `property-ai-dev` |
| test | `test` | `property_ai_test` :3307 | `/2` | `property_ai.test` | `knowledge_chunks_test` | `property-ai-test` |
| production-like | `staging` | `property_ai_staging` :3316 | `/3` | `property_ai.staging` | `knowledge_chunks_staging` | `property-ai-staging` |
| production | `production` | 生产库名 | `/4` | `property_ai.production` | `knowledge_chunks_bge_m3_v1` | `property-ai-prod` |

覆盖队列名：设置 `CELERY_TASK_QUEUE`。未设置时为 `property_ai.{APP_ENV}`。

Worker **必须** `celery worker -Q $CELERY_TASK_QUEUE`。不要监听默认的历史队列 `analyze_work_order_task`，也不要同时消费 `.dlq`。

Production Worker 不得与 Development/Test 共用：

- 同一 RabbitMQ vhost + 同一 queue 名
- 同一 Milvus collection
- 同一 MinIO bucket
- 同一 Redis DB
- 同一 MySQL schema

Day 2 问题：Worker 启动后消费了旧的 `analyze_work_order_task` backlog。Day 3 通过环境队列名 + `-Q` 隔离。旧 backlog 留在原队列，新 Worker 不会读取。

---

## 2. Secrets

所有 Production Secret 只允许来自：

1. 环境变量
2. Secret manager
3. 挂载的 secret 文件（权限仅部署用户可读）

禁止出现在：Docker image、git、小程序前端、日志、AuditLog。

至少检查：

- `APP_SECRET_KEY`
- `WECHAT_APP_SECRET`（只在后端）
- `LLM_API_KEY` / VLM / ASR / Embedding / Reranker keys
- `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY`
- `DATABASE_URL` / MySQL password
- `RABBITMQ_PASSWORD`

复制 `.env.example` → `.env`。不要提交 `.env`。不要把 App Secret 写进 `miniprogram/`。

TLS 私钥只放主机 `/etc/nginx/certs/privkey.pem`，仓库只有 `deploy/nginx-tls.conf.example`。

---

## 3. 启动顺序（强制）

1. Infrastructure：MySQL、Redis、RabbitMQ、etcd、MinIO、Milvus
2. Verify MySQL：`mysqladmin ping`，确认目标库为空或已备份
3. `cd backend && alembic heads` 核对交付版本的迁移目标，再在已确认空库或已备份的目标库执行 `alembic upgrade head`。当前源码单一 head 为 `20261003_0015_work_order_media_cycle_links`；升级后 `alembic current` 必须与该交付版本一致。
4. Start API（不要 migrate，不要 seed）
5. Start Worker（`-Q property_ai.{env}`）
6. Start Nginx
7. `GET /healthz` 与 `GET /api/v1/healthz`；`GET /readyz` 与 `GET /api/v1/readyz`
8. Smoke：未认证 `GET /api/v1/opsz` 应为 401；operator token 下确认 `celery_queue` / `milvus_collection` / `storage_bucket` 属于当前环境

Production-like 排练使用独立 Compose 项目，不碰 development 卷：

```bash
docker compose -f deploy/docker-compose.prodlike.yml down
docker compose -f deploy/docker-compose.prodlike.yml up -d --build
```

然后在 **空库** 上迁移（不要 `dev_seed`）：

```bash
cd backend
$env:DATABASE_URL="mysql+pymysql://property:change-me@127.0.0.1:3316/property_ai_staging?charset=utf8mb4"
$env:APP_ENV="staging"
alembic upgrade head
alembic current
```

期望与本交付版本 `alembic heads` 一致；当前为 `20261003_0015_work_order_media_cycle_links`。旧 `20260917_0007` 仅是历史阶段版本，不能作为当前完整部署成功标准。

Development 栈继续用 `deploy/docker-compose.yml`。`docker compose down`（无 `-v`）只停容器，保留 `mysql_data`。

---

## 4. 配置 `.env`

最小 Development：

```
APP_ENV=development
APP_SECRET_KEY=<long random>
DATABASE_URL=mysql+pymysql://property:change-me@127.0.0.1:3306/property_ai?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=amqp://property:change-me@127.0.0.1:5672/property_ai
CELERY_TASK_QUEUE=property_ai.development
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=knowledge_chunks_v1
```

Production 额外：

- `APP_ENV=production`
- 非默认 `APP_SECRET_KEY`
- `DEV_AUTH_ENABLED=false`
- 若 `WECHAT_LOGIN_ENABLED=true`：真实 `WECHAT_APP_ID` + `WECHAT_APP_SECRET`
- 若启用 production providers：对应 BASE_URL / API_KEY / STORAGE_* 必须齐全，否则进程 fail-fast

SQLAlchemy V1 默认：`DB_POOL_SIZE=5`、`DB_MAX_OVERFLOW=10`、`DB_POOL_RECYCLE_SECONDS=1800`、`pool_pre_ping=true`、会话 `SET time_zone='+00:00'`。

---

## 5. Nginx / HTTPS

HTTP 排练：`deploy/nginx.conf` 反代 `/api/`、`/healthz`、`/readyz` 到 API。

```
HTTPS CONFIGURATION = READY
LIVE TLS = BLOCKED_BY_DOMAIN_CERT
```

直到企业签发真实域名证书前，不要把 live HTTPS 报成 PASS。

启用 TLS：复制 `deploy/nginx-tls.conf.example`，替换 `server_name`，挂载 `fullchain.pem` / `privkey.pem`。模板已包含：

- `server_name`
- certificate paths
- HSTS / nosniff / DENY frame
- `client_max_body_size 12m`（应用 `UPLOAD_MAX_MB=10`）
- `proxy_read_timeout 60s`
- `X-Request-Id` 转发

---

## 6. 微信小程序合法域名（Production）

没有正式 AppID / 备案域名时：**不要声称真机 PASS**。

小程序只配置 API origin，**禁止** App Secret。

| 能力 | 需要 |
| --- | --- |
| request 合法域名 | `https://<api-host>`（微信要求 HTTPS） |
| uploadFile 合法域名 | 同上；若使用 presigned MinIO/OSS，另加对象存储 HTTPS 域名 |
| downloadFile 合法域名 | 同上 |
| TLS | 可信 CA 证书；不要自签应付微信 |

微信公众平台 → 开发 → 开发管理 → 服务器域名：

1. request：`https://api.example.com`
2. uploadFile：`https://api.example.com`（推荐走后端 `/files/{id}/content` proxy，避免把存储域名暴露给客户端）
3. downloadFile：`https://api.example.com`
4. 不要配置 `ws://` / `http://`

`miniprogram/config/env.local.js` 的 `release` 填 `https://api.example.com/api/v1`。

---

## 7. RabbitMQ / Celery

- `task_acks_late=True`
- `task_reject_on_worker_lost=True`
- `worker_prefetch_multiplier=1`
- 分析/知识 ingest `max_retries=2`；通知 `max_retries=3`
- 队列：`property_ai.{env}`；观测队列名：`property_ai.{env}.dlq`（失败日志 `celery_task_dead_lettered`）
- enqueue 使用 `apply_async(..., queue=resolved_celery_queue)`

Worker 重启时任务可能 redelivery。幂等：

- WorkOrder 分析：已有终态则 `already_final` skip
- Knowledge ingest：已 `SUCCEEDED` 且非 rebuild 则 skip
- Notification delivery：非 `PENDING` 则 skip

不要指望“只投递一次”。要靠业务幂等。

检查队列深度（RabbitMQ management，prodlike 映射 `15673`）：

```bash
docker compose -f deploy/docker-compose.prodlike.yml exec rabbitmq rabbitmqctl list_queues -p property_ai_staging name messages
```

---

## 8. Backup / Restore（MySQL）

逻辑备份（UTC）：

```bash
python scripts/ops/mysql_backup.py --container <mysql-container> --database property_ai --out backup.sql
```

Windows / 演练（test 实例，可销毁）：

```bash
python scripts/ops/mysql_backup.py --container property-ai-mysql-test-1 --user root --password-env PROPERTY_AI_MYSQL_PASSWORD --database property_ai_test --out tmp/backup-test.sql
python scripts/ops/mysql_restore.py --container property-ai-mysql-test-1 --user root --password-env PROPERTY_AI_MYSQL_PASSWORD --database property_ai_restore_drill --dump tmp/backup-test.sql --create-database
```

凭证由本机秘密管理方式注入 `PROPERTY_AI_MYSQL_PASSWORD`（默认变量名），不在命令行填写真实值；旧 `--password` 参数已禁用。脚本仅将密码放入 Docker 子进程环境，不打印外部 stderr，不能据此宣称本机管理员无法读取凭证。备份目标已存在则拒绝，失败留下的文件须核验，不能作为成功备份使用。恢复前核对实际 Docker context、具名容器及可销毁数据库；仅使用可信转储，数据库标识符校验不拦截转储内部的任意 SQL。恢复超时/失败可能已部分生效，必须检查实际目标状态，禁止自动重复执行。

Restore 之后：

```bash
alembic current
```

核对 `alembic_version`、`users` / `work_orders` / `resident_house_relations` 行数。

**禁止**把 restore 指向 Development `property_ai` 或真实 Production，除非变更窗口 + 已确认备份。

---

## 9. Milvus 重建（derived index）

Milvus **不是**知识权威源。权威是 MySQL `knowledge_documents` / `knowledge_document_versions.source_text`。

丢失向量后：

```bash
python -m app.cli.rebuild_knowledge_index
```

会按 ACTIVE 文档 `rebuild=True` 重新 ingest。RAG citation 应恢复。工单/考勤/公告在 Milvus down 时仍可用（readyz 为 degraded）。

---

## 10. 对象存储恢复假设（必须理解）

**数据库备份不等于媒体文件备份。**

`file_objects` 只存 metadata + opaque `storage_key`。对象字节在 MinIO / OSS。MySQL restore **不能**还原图片/音频 bytes。

生产必须由存储运营商提供：

- bucket durability（多副本 / 纠删码）
- versioning 或跨区域复制
- 独立于 mysqldump 的对象备份策略

恢复顺序：先恢复 MySQL metadata，再确认 bucket 中对应 key 仍存在。缺失对象时 API 返回存储错误，不要从数据库“编造”文件。

---

## 11. 优雅重启

```bash
docker compose -f deploy/docker-compose.prodlike.yml restart api
docker compose -f deploy/docker-compose.prodlike.yml restart worker
docker compose -f deploy/docker-compose.prodlike.yml exec nginx nginx -s reload
```

期望：

- 不破坏数据库、不改变 `alembic_version`
- 已 commit 的 WorkOrder 仍在
- Worker 进行中的 task：ACK late + 幂等，不 double-mutate 业务状态
- Nginx reload 不丢已建立的反代配置

---

## 12. 监控基线（无 Prometheus）

| 检查 | 怎么做 |
| --- | --- |
| container health | `docker compose ps` |
| API liveness | `GET /healthz` 或 `/api/v1/healthz` → `status=ok` |
| API readiness | `GET /readyz`；MySQL down → 503 `not_ready`；AI/RAG down → 200 `degraded` |
| 运行身份 | `GET /api/v1/opsz`（无密钥） |
| Worker | `celery -A app.worker.celery_app.celery_app inspect ping` |
| RabbitMQ depth | `rabbitmqctl list_queues` |
| DB | readyz mysql probe；`SELECT 1` |
| Milvus | readyz milvus probe（non-critical） |
| 错误日志 | JSON：`timestamp` `level` `request_id` `service` `operation` `error_code` |

不要在日志中寻找 JWT、Authorization、password、WeChat code、session_key、App Secret、Provider API Key。

---

## 13. Failure drills 期望

| 故障 | healthz | readyz | 人工工单主链 |
| --- | --- | --- | --- |
| Redis down | 200 | 200 degraded | 可用（锁/缓存能力下降） |
| RabbitMQ down | 200 | 200 degraded | 工单创建成功，AI 入队失败可观测，进入人工队列 |
| Milvus down | 200 | 200 degraded | 工单可用；知识助手降级 |
| LLM / Embedding down | 200 | 200 degraded | 工单创建成功 |
| Storage down | 200 | 200 degraded | 无附件工单仍可创建 |
| Worker down | 200 | 200（worker 不在 readyz） | 工单已 commit；任务堆积在隔离 queue |
| MySQL down | 200 | **503 not_ready** | 业务不可写 |

AI down **不得**把整个系统变成 503。

---

## 14. 排障

1. `GET /api/v1/opsz` 确认当前 env / queue / collection / bucket
2. `GET /api/v1/readyz` 看哪个 dependency `down`
3. 用 `request_id` 搜 JSON 日志
4. Worker：确认 `-Q` 与 `opsz.celery_queue` 一致
5. 若 Worker 在消费错误环境的任务：立刻停 Worker，核对 `APP_ENV` / vhost / queue，不要用 Production Worker 去 drain Development backlog
6. 迁移问题：`alembic current` / `alembic history`；**禁止改 0001–0007**
7. 存储 404：查 `file_objects.storage_key` 是否在 bucket；不要指望 MySQL backup 找回 bytes

---

## 15. Production-like E2E smoke

在完整 Compose（nginx + api + worker + mysql + redis + rabbitmq + minio + milvus）上：

1. Fake WeChat login（`WECHAT_PROVIDER=development`）
2. Resident / House / WorkOrder
3. Fake AI analysis（development LLM）
4. Dispatch → Staff processing → Resident confirm
5. Announcement 人工审核
6. Knowledge ingest + RAG citation
7. Notification outbox

重点证明进程与依赖在一起工作，而不是再测一遍业务规则。设置：

```
DAY3_PRODLIKE_BASE_URL=http://127.0.0.1:8081
```

可跑 `tests/integration/test_day3_prodlike_e2e.py` 的 live 段。默认 pytest 不要求该环境。

---

## 16. Day 4 disposable cross-process stack

独立 Compose 项目 `property-ai-day4`，服务名 `api-day4` / `worker-day4` / `nginx-day4`，避免与 development 的 `api` DNS 冲突。

| 项 | 值 |
| --- | --- |
| APP_ENV | test |
| MySQL | mysql-test / `property_ai_day4` |
| Redis | `/5` |
| RabbitMQ | user `day4`，vhost `property_ai_day4`，queue `property_ai.day4` |
| Milvus | `knowledge_chunks_day4` |
| MinIO | `property-ai-day4` |
| Nginx | `http://127.0.0.1:8082` |
| Providers | Development/Fake，明确标注 |

失败任务：Redis `property_ai:{env}:failed_tasks` + 命名 DLQ 发布 + `python -m app.cli.replay_failed_task`。这不是 AMQP DLX。未实际投递/重放时不得写“死信已验证”。

`/opsz` 需要已认证且具备 **SYSTEM 范围** 的 `audit:read`；PROPERTY/TENANT 业务管理授权不能查看全局部署详情。公网未认证应为 401。本机演练 Nginx 端口绑定 `127.0.0.1`（8080/8081/8082）。readyz 未探测的收费模型必须是 `not_checked`，不得标 `up`。

## 17. 分析任务数据库恢复边界（025）

先定位领取/分析/结果提交/派单/失败记录中的准确阶段，区分未提交与提交响应不确定。MySQL分析白名单1205/1213/2006/2013在新连接/事务重读版本、任务所有者、已存分析及Assignment；最多3次总尝试，不全异常重试、不增加数据库超时掩盖问题。`AI_TASK_ATTEMPT`是既有工单日志中的追加任务事实，不是新任务平台；`AI_TASK_FAILED`配合稳定错误及用户提示表达耗尽。

025仅以新合成对象验证prepare/employee reservation真实2013、明确标注的提交后丢确认模拟、取消和有限耗尽。提交后丢确认模拟不等于覆盖所有真实COMMIT网络不确定性；进程丢失、未入队、双基础设施故障、历史失败恢复仍需独立演练。禁止从日志中看到retry字样就宣布可靠性全部通过。

AUTH001本轮只读保护12/24/25及历史5条DLQ；不读消息、不重放、不清理。故障夹具仅绑定新run与操作，禁止停共享MySQL/全局网络或全库Beat/sweeper。Worker故障控制只属于reports演练入口，结束后确认idle并恢复无控制的Development Worker。数据库版本仍0011，无Migration；真实Provider与Production验收不随本轮变化。

