"""Prepare disposable Day 4 E2E database, vhost, schema, and seed data.

Never touches development MySQL volume, knowledge_chunks_v1, or the
development Celery vhost.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)


def _run_ok(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def main() -> int:
    mysql_name = "property-ai-mysql-test-1"
    rabbit_name = "property-ai-rabbitmq-1"
    mysql_running = _run_ok(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=property-ai-mysql-test-1"]
    )
    rabbit_running = _run_ok(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=property-ai-rabbitmq-1"]
    )
    if mysql_name not in mysql_running.stdout or rabbit_name not in rabbit_running.stdout:
        print("property-ai-mysql-test-1 and property-ai-rabbitmq-1 must be running", file=sys.stderr)
        return 1

    _run(
        [
            "docker",
            "exec",
            mysql_name,
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            "-e",
            "CREATE DATABASE IF NOT EXISTS property_ai_day4 CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; GRANT ALL ON property_ai_day4.* TO 'property_test'@'%';",
        ]
    )
    _run_ok(["docker", "exec", rabbit_name, "rabbitmqctl", "add_user", "day4", "change-me-day4"])
    _run_ok(["docker", "exec", rabbit_name, "rabbitmqctl", "add_vhost", "property_ai_day4"])
    _run(
        [
            "docker",
            "exec",
            rabbit_name,
            "rabbitmqctl",
            "set_permissions",
            "-p",
            "property_ai_day4",
            "day4",
            ".*",
            ".*",
            ".*",
        ]
    )
    _run_ok(
        [
            "docker",
            "exec",
            rabbit_name,
            "rabbitmqctl",
            "clear_permissions",
            "-p",
            "property_ai",
            "day4",
        ]
    )
    _run_ok(
        [
            "docker",
            "exec",
            rabbit_name,
            "rabbitmqctl",
            "set_permissions",
            "-p",
            "property_ai",
            "day4",
            "^$",
            "^$",
            "^$",
        ]
    )
    _run_ok(
        [
            "docker",
            "exec",
            rabbit_name,
            "rabbitmqctl",
            "clear_permissions",
            "-p",
            "property_ai",
            "day4",
        ]
    )

    env = os.environ.copy()
    for key in (
        "APP_ENV",
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "DEV_AUTH_ENABLED",
        "CELERY_BROKER_URL",
        "CELERY_TASK_QUEUE",
        "REDIS_URL",
        "MILVUS_COLLECTION",
        "STORAGE_BUCKET",
        "AI_ENQUEUE_ON_CREATE",
    ):
        env.pop(key, None)
    env.update(
        {
            "APP_ENV": "test",
            "APP_SECRET_KEY": "day4-e2e-secret-that-is-long-enough",
            "DEV_AUTH_ENABLED": "true",
            "WECHAT_LOGIN_ENABLED": "true",
            "DATABASE_URL": "mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4",
            "TEST_DATABASE_URL": "mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_day4?charset=utf8mb4",
        }
    )
    python = sys.executable
    _run([python, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env)
    _run([python, "-m", "app.cli.dev_seed"], cwd=BACKEND, env=env)
    _run([python, "-m", "app.cli.dev_house_fixture"], cwd=BACKEND, env=env)
    _run([python, "-m", "app.cli.dev_employee_fixture"], cwd=BACKEND, env=env)
    print("Day 4 disposable MySQL, RabbitMQ vhost, schema, and seed are ready")
    print("Next: docker compose -f deploy/docker-compose.day4.yml up -d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
