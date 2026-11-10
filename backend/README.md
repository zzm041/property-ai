# 后端

Python 3.12 / FastAPI 模块化单体，配合 Celery Worker 处理异步任务。应用入口为 `app/main.py`；冻结依赖见 [pyproject.toml](pyproject.toml)。

## 分层

- `api` / `schema`：HTTP 接口、输入与响应模型。
- `domain` / `service`：业务规则、权限判断、状态转换和事务边界。
- `repository` / `model`：SQLAlchemy 数据访问与持久化模型。
- `provider`：短信、模型、语音、存储等外部系统适配。
- `worker` / `core`：异步任务、配置、日志和基础设施。

主要能力包括账户与房屋绑定、员工与服务范围、工单闭环、规则派单、AI 结构化、权限隔离知识问答、公告及审计。数据库使用同步 Session 和 PyMySQL；数据库主导接口避免在异步函数中直接执行同步 IO。

## 测试与运行

在仓库根目录使用项目 Python 环境运行专项测试，例如：

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m pytest backend/tests/unit/test_knowledge_retrieval_scope.py -q
```

单元测试、隔离 MySQL 集成测试、真实模型评测和本地部署验收分别记录。单元测试通过不代表外部 Provider 或正式部署可用。集成测试必须使用明确隔离的测试库，不能指向现有业务库。

启动和环境准备遵循[部署手册](../deploy/README.md)及[运维手册](../docs/11-OPERATIONS.md)。迁移单独执行，不在 API 启动时自动修改数据库。业务规格见 [docs](../docs/00-PROJECT-SCOPE.md)，评测口径见[业务评测说明](../docs/12-BUSINESS-EVALUATION.md)。
