"""Create exclusive RabbitMQ user+vhost for AUTH001. Does not recreate containers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
OPS = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from auth001_isolated_runtime import (
    ISOLATED_BROKER_USER,
    ISOLATED_VHOST,
    RABBIT_CONTAINER_FILTER,
    isolated_broker_password,
)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def main() -> int:
    broker_password = isolated_broker_password()
    running = _run(
        [
            "docker",
            "ps",
            "--format",
            "{{.Names}}",
            "--filter",
            f"name={RABBIT_CONTAINER_FILTER}",
        ]
    )
    names = [line.strip() for line in running.stdout.splitlines() if line.strip()]
    if running.returncode != 0 or RABBIT_CONTAINER_FILTER not in names:
        print(
            "property-ai-rabbitmq-1 must be running; this script will not recreate it",
            file=sys.stderr,
        )
        return 1
    container = RABBIT_CONTAINER_FILTER
    _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "add_user",
            ISOLATED_BROKER_USER,
            broker_password,
        ]
    )
    _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "change_password",
            ISOLATED_BROKER_USER,
            broker_password,
        ]
    )
    created = _run(
        ["docker", "exec", container, "rabbitmqctl", "add_vhost", ISOLATED_VHOST]
    )
    if created.returncode not in {0, 70} and "already exists" not in (
        created.stderr + created.stdout
    ).casefold() and created.returncode != 0:
        # add_vhost returns 0 or already-exists; continue to permissions either way
        pass
    permitted = _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "set_permissions",
            "-p",
            ISOLATED_VHOST,
            ISOLATED_BROKER_USER,
            ".*",
            ".*",
            ".*",
        ]
    )
    if permitted.returncode != 0:
        sys.stderr.write(permitted.stdout + permitted.stderr)
        return permitted.returncode
    _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "clear_permissions",
            "-p",
            "property_ai",
            ISOLATED_BROKER_USER,
        ]
    )
    _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "clear_permissions",
            "-p",
            "property_ai_day4",
            ISOLATED_BROKER_USER,
        ]
    )
    vhosts = _run(["docker", "exec", container, "rabbitmqctl", "list_vhosts", "name"])
    users = _run(
        [
            "docker",
            "exec",
            container,
            "rabbitmqctl",
            "list_user_permissions",
            ISOLATED_BROKER_USER,
        ]
    )
    if ISOLATED_VHOST not in vhosts.stdout:
        print("vhost was not created", file=sys.stderr)
        return 1
    if ISOLATED_VHOST not in users.stdout:
        print("auth001 permissions were not granted", file=sys.stderr)
        return 1
    print(
        f"isolated_broker_ready vhost={ISOLATED_VHOST} user={ISOLATED_BROKER_USER}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
