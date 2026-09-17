# scripts

运维脚本（Day 3）与 Day 4 一次性验证。不要对 development `property_ai` 做未经确认的 restore。不要删除 Development Milvus collection。

```bash
python scripts/ops/mysql_backup.py --container <mysql> --database property_ai_test --user root --password change-me-test-root --out tmp/backup.sql
python scripts/ops/mysql_restore.py --container <mysql> --database property_ai_restore_drill --user root --password change-me-test-root --dump tmp/backup.sql --create-database
```

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
python scripts/ops/perf_probe.py --base-url http://127.0.0.1:8082 --concurrency 20 --requests 100
```

Production-like 排练：

```bash
docker compose -f deploy/docker-compose.prodlike.yml up -d --build
```
