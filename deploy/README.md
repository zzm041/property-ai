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
