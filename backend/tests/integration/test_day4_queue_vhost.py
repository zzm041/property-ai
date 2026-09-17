"""RabbitMQ vhost/user ACL proof. Queue names alone are not isolation."""

import subprocess

import pytest


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


def _rabbitmq_container() -> str | None:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=rabbitmq"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for preferred in ("property-ai-rabbitmq-1",):
        if preferred in names:
            return preferred
    return names[0] if names else None


@pytest.mark.skipif(not _docker_available(), reason="docker is not available")
def test_day4_user_cannot_access_development_vhost() -> None:
    container = _rabbitmq_container()
    if container is None:
        pytest.skip("rabbitmq container is not running")
    listed = subprocess.run(
        ["docker", "exec", container, "rabbitmqctl", "list_users"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if listed.returncode != 0 or "day4" not in listed.stdout:
        pytest.skip("day4 RabbitMQ user is not provisioned")
    own = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "list_permissions",
            "-p",
            "property_ai_day4",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert own.returncode == 0, own.stderr
    assert "day4" in own.stdout
    shared = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "list_user_permissions",
            "day4",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert shared.returncode == 0, shared.stderr
    vhosts: list[str] = []
    for line in shared.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Listing") or stripped.startswith("vhost"):
            continue
        vhosts.append(stripped.split()[0])
    assert "property_ai_day4" in vhosts
    assert "property_ai" not in vhosts
