# 部署占位

Production V1 使用 Docker Compose + Nginx。镜像版本见 `docs/09-DEPLOYMENT.md`。

当前阶段 **不提交可运行的 compose 文件**，避免未确认就拉起全套中间件。

Day 1（需负责人确认后）将添加：

- `deploy/docker-compose.yml`：MySQL 8.0.41、Redis 7.4.2、RabbitMQ 3.13.7
- `deploy/nginx/nginx.conf`：反向代理占位
- API / Worker 镜像延后到应用可启动之后

Milvus 不在 Day 1 启动，放到知识助手模块。
