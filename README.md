# 智慧物业 AI 运营与工单调度平台

真实物业企业 **Production V1** 后端 + 微信小程序。  
当前状态：**Stage 0.5 已完成，等待重新生成并确认 Day 1 Task 1~8 计划**。规格与元数据已统一，业务代码尚未开始。

## 当前不要期望什么

- 现在没有可登录的 API，也没有可运行的小程序页面。
- 本期只冻结范围、角色、领域模型、状态机、技术栈和目录。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/00-PROJECT-SCOPE.md](docs/00-PROJECT-SCOPE.md) | 产品定位、范围、明确不做 |
| [docs/01-PRD.md](docs/01-PRD.md) | 功能需求 |
| [docs/02-ROLES-PERMISSIONS.md](docs/02-ROLES-PERMISSIONS.md) | 角色与权限 |
| [docs/03-DOMAIN-MODEL.md](docs/03-DOMAIN-MODEL.md) | 核心实体与 ER |
| [docs/04-WORKORDER-STATE-MACHINE.md](docs/04-WORKORDER-STATE-MACHINE.md) | 工单状态机 |
| [docs/05-AI-DISPATCH-SPEC.md](docs/05-AI-DISPATCH-SPEC.md) | AI 理解与派单 |
| [docs/06-API-CONTRACT.md](docs/06-API-CONTRACT.md) | API 契约初稿 |
| [docs/07-SECURITY-PRIVACY.md](docs/07-SECURITY-PRIVACY.md) | 安全与隐私 |
| [docs/08-NON-FUNCTIONAL.md](docs/08-NON-FUNCTIONAL.md) | 非功能与架构 |
| [docs/09-DEPLOYMENT.md](docs/09-DEPLOYMENT.md) | 部署与版本冻结 |
| [docs/10-ACCEPTANCE.md](docs/10-ACCEPTANCE.md) | 验收标准 |
| [docs/BACKLOG.md](docs/BACKLOG.md) | V1 后 backlog |

Agent 工作协议见 [AGENTS.md](AGENTS.md)。

## 技术栈（冻结）

Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + MySQL 8 + Redis + RabbitMQ/Celery + Milvus/bge-m3 + 微信原生小程序 + Docker Compose/Nginx。

版本见 `backend/pyproject.toml` 与 `docs/09-DEPLOYMENT.md`。

## Day 1 前置条件（Stage 0.5 不执行）

1. 本机 Python 必须安装并切换到 **3.12.11**；版本不匹配时 Task 1 不得判定 PASS。
2. 复制 `.env.example` 为 `.env`。
3. 重新生成并确认 Day 1 Task 1~8 计划后，从 Task 1 开始逐项实施，不得一次执行全部 Task。

新的 Day 1 覆盖基础设施、组织房产、Development Auth/RBAC、住户绑定、员工多小区关系、签到、小程序最小纵向链路及完整集成验收。Milvus、RAG、AI、WorkOrder、Incident、SLA、正式微信/短信/实名和 Production Nginx 不属于 Day 1。
