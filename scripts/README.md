# scripts

`scripts/selfhost/` 提供随交付包携带的私有 AI 配置、离线模型/依赖预检和受限真实推理入口。使用方式与尚未验证的边界见 `deploy/selfhost/README.md`；不要将旧 `scripts/ops/local_ai_http.py` 的历史验收启动方式当作客户安装流程。

运维脚本（Day 3）与 Day 4 一次性验证。不要对 development `property_ai` 做未经确认的 restore。不要删除 Development Milvus collection。

```bash
python scripts/ops/mysql_backup.py --container <mysql> --database property_ai_test --user root --password-env PROPERTY_AI_MYSQL_PASSWORD --out tmp/backup.sql
python scripts/ops/mysql_restore.py --container <mysql> --database property_ai_restore_drill --user root --password-env PROPERTY_AI_MYSQL_PASSWORD --dump tmp/backup.sql --create-database
```

运行前通过本机秘密管理方式提供 `PROPERTY_AI_MYSQL_PASSWORD`，不要把真实值写进命令、脚本或报告。旧 `--password` 已拒绝，Docker 子进程通过环境变量传递凭证；这不隔离有权限检查进程环境的本机管理员。备份不会覆盖已有文件，失败或中断后保留的文件不能当成完整备份。恢复只接受可信转储，并须先核实 Docker context、容器和可销毁目标；库名校验不限制转储内的 SQL，不能用来沙箱运行不可信 SQL。恢复失败/超时可能已部分执行，须核查后处理，不自动重试。

知识索引重建（在 backend 目录，正确的 APP_ENV / DATABASE_URL / MILVUS_*）：

```bash
python -m app.cli.rebuild_knowledge_index
python -m app.cli.replay_failed_task --list
```

Day 4 一次性栈：

```bash
python scripts/ops/day4_prepare.py
docker compose -f deploy/docker-compose.day4.yml up -d
python scripts/ops/day4_e2e.py
python scripts/ops/day4_failed_task_drill.py
python scripts/ops/day4_perf_run.py
python scripts/ops/perf_probe.py --base-url http://127.0.0.1:8082 --mode cold --concurrency 20 --requests 100
```

Production-like 排练：

```bash
docker compose -f deploy/docker-compose.prodlike.yml up -d --build
```
