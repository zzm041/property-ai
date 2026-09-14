# 部署占位

Production V1 使用 Docker Compose + Nginx。镜像版本见 `docs/09-DEPLOYMENT.md`。

Stage 0.5 **不提交或修改可运行的 Compose / Nginx 文件**。

新的 Day 1 Task 1（需单独确认后）将添加开发基础设施：

- `deploy/docker-compose.yml`：MySQL 8.0.41、Redis 7.4.2、RabbitMQ 3.13.7
- API / Worker 镜像延后到应用可启动之后

Milvus、RAG、LLM、VLM、ASR 和 Production Nginx 不在 Day 1 启动或实现。Production Nginx 仍是 V1 部署目标，但须在后续批准阶段完成。
