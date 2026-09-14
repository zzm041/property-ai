# 阶段 0/1 推荐目录（无业务实现）

```text
property-ai/
  AGENTS.md
  README.md
  .env.example
  .gitignore
  .python-version
  .cursor/rules/project.mdc
  docs/                          # 规格，先于代码
  backend/
    pyproject.toml               # 冻结依赖
    alembic.ini                  # Day 1 Task 2 初始化
    alembic/versions/            # Stage 0.5 为空，Task 2 创建首个真实迁移
    app/
      api/                       # FastAPI 路由
      domain/                    # 状态机、派单规则
      service/                   # 用例
      repository/                # 持久化
      model/                     # SQLAlchemy
      schema/                    # Pydantic
      provider/                  # 外部系统抽象 + dev/prod
      worker/                    # Celery 任务
      core/                      # 配置、日志、错误码
    tests/
      unit/
      integration/
  miniprogram/                   # Stage 0.5 占位，Day 1 Task 7 最小纵向链路
  deploy/                        # Day 1 Task 1 开发 Compose；Production Nginx 后续
  scripts/
```

Stage 0.5 只统一规格、依赖元数据与占位说明。**不要**在此时生成 Route、ORM、Service、Repository、Migration 或小程序业务页面。
