# 阶段 0/1 推荐目录（无业务实现）

```text
property-ai/
  AGENTS.md
  README.md
  .env.example
  .gitignore
  .python-version
  .cursor/rules/project.mdc
  docs/                          # 正式工程规格；需求与验收以此为准
  reports/                       # 阶段性工程验收证据，不含面试问答
  learning/                      # 个人学习笔记；不覆盖 docs，不进入运行时/镜像/RAG
  backend/
    pyproject.toml               # 冻结依赖
    alembic.ini
    alembic/versions/            # 0001–0007 已冻结，禁止改历史
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
  miniprogram/                   # 微信原生小程序
  deploy/                        # Compose + Nginx；Day 4 一次性栈见 docker-compose.day4.yml
  scripts/                       # 运维与一次性验证脚本
```

`learning/` 不是业务需求来源，不被运行时代码读取，不进入生产镜像和小程序发布包。
验收证据写入 `reports/`，不要把面试问答混进工程报告。
