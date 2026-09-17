"""Disposable MySQL dump/restore drill against the test instance. Never touches development."""

import subprocess

import pytest
from sqlalchemy import text

RESTORE_DB = "property_ai_restore_drill"


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _mysql_test_container() -> str | None:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--format",
            "{{.Names}}",
            "--filter",
            "name=mysql-test",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return None
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names[0] if names else None


def _exec(container: str, args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-i", container, *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(not _docker_available(), reason="docker is not available")
def test_mysql_dump_restore_preserves_alembic_and_counts(test_engine) -> None:
    container = _mysql_test_container()
    if container is None:
        pytest.skip("mysql-test container is not running")

    with test_engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        user_count = connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one()

    dump = _exec(
        container,
        [
            "mysqldump",
            "--single-transaction",
            "-uroot",
            "-pchange-me-test-root",
            "property_ai_test",
        ],
    )
    assert dump.returncode == 0, dump.stderr
    assert "alembic_version" in dump.stdout

    prepared = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            "-e",
            f"DROP DATABASE IF EXISTS `{RESTORE_DB}`; CREATE DATABASE `{RESTORE_DB}` CHARACTER SET utf8mb4;",
        ],
    )
    assert prepared.returncode == 0, prepared.stderr

    restored = _exec(
        container,
        ["mysql", "-uroot", "-pchange-me-test-root", RESTORE_DB],
        input_text=dump.stdout,
    )
    assert restored.returncode == 0, restored.stderr

    verify = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            RESTORE_DB,
            "-N",
            "-e",
            "SELECT version_num FROM alembic_version; SELECT COUNT(*) FROM users;",
        ],
    )
    assert verify.returncode == 0, verify.stderr
    lines = [line.strip() for line in verify.stdout.splitlines() if line.strip()]
    assert revision in lines
    assert str(int(user_count)) in lines

    cleanup = _exec(
        container,
        [
            "mysql",
            "-uroot",
            "-pchange-me-test-root",
            "-e",
            f"DROP DATABASE IF EXISTS `{RESTORE_DB}`",
        ],
    )
    assert cleanup.returncode == 0, cleanup.stderr

    # Original disposable test schema remains intact.
    with test_engine.connect() as connection:
        still = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert still == revision
