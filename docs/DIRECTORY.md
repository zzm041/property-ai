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
    alembic.ini                  # Day 1 再填真实 URL
    alembic/versions/            # 空，待首个迁移
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
  miniprogram/                   # 微信原生小程序，阶段 0 仅占位
  deploy/                        # Compose / Nginx，Day 1 再写可运行文件
  scripts/
```

阶段 0 只建目录与说明文件。**不要**在此时生成完整 CRUD、状态机实现或假数据迁移。
