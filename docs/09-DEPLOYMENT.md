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

## 5. 迁移与备份

- 发布流程：备份 → `alembic upgrade head` → 启动 API/Worker → 健康检查
- 应用与 Alembic 统一使用 PyMySQL；所有 Schema 变化必须通过历史可追踪的 Alembic Migration
- 禁止 `create_all` 代替 Migration，禁止删除历史 Migration 解决冲突
- MySQL：每日 `mysqldump` 或 xtrabackup（生产选定一种写入运行手册）+ binlog 至少 7 天
- 文件存储：对象存储版本或每日同步
- 恢复：在 staging 用备份演练，阶段 1 只要求文档化，Day 1 不做真实恢复演练

## 6. Git

仓库根目录为 Git 项目根。当前开发机若未安装 Git，负责人需先安装后再 `git init`。不要把 `.env`、数据卷、真实备份提交进库。
