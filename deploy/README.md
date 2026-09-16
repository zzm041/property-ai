# 部署

Production V1 使用 Docker Compose + Nginx。镜像版本见 `docs/09-DEPLOYMENT.md`。

`deploy/docker-compose.yml` 包含：

- MySQL 8.0.41
- Redis 7.4.2
- RabbitMQ 3.13.7
- etcd + MinIO + Milvus 2.5.10
- API / Celery worker（profile `app`）
- Nginx（profile `app`）

启动数据面：

```bash
docker compose -f deploy/docker-compose.yml --profile test up -d mysql mysql-test redis rabbitmq etcd minio milvus
```

发布应用前必须显式执行：

```bash
cd backend
alembic upgrade head
```

不要在 API 启动时自动 migrate。不要把 Secret bake 进镜像；用环境变量注入。生产禁止 `dev_seed`。
