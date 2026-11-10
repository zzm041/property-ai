"""PA-116 exact four-container cutover (api/worker/publisher/beat).

The generic `docker compose up` path is deliberately guarded (retained rollback
containers must survive). This tool therefore performs the same operation the
2026-11-03 closeout used: create a replacement container per service from the
*inspected* configuration of the running one, start it, verify it, and only then
stop + rename the original to `-rollback-<ts>`.

Usage:
    python cutover_delivery.py plan  <image>
    python cutover_delivery.py apply <image> [--env KEY=VALUE ...]
    python cutover_delivery.py verify
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT = "property-ai-delivery-check"
SERVICES = ("api", "worker", "publisher", "beat")
NETWORK = f"{PROJECT}_default"
BEAT_VOLUME = f"{PROJECT}_beat_state"
HERE = Path(__file__).resolve().parent
TMP = HERE / "tmp"

#: Health probes copied verbatim from deploy/docker-compose.delivery.yml. The
#: inspected value can already be mis-quoted by an earlier cutover, so the
#: compose file stays the authority for services that declare one.
CANONICAL_HEALTH_CMD = {
    "api": (
        "python -c \"import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=3)\""
    ),
}
KEEP_LABELS = (
    "com.docker.compose.project",
    "com.docker.compose.service",
    "com.docker.compose.container-number",
    "com.docker.compose.oneoff",
    "com.docker.compose.version",
    "com.docker.compose.project.working_dir",
    "com.docker.compose.project.config_files",
    "property-ai.source-sha",
)


def docker(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} -> {proc.returncode}: {(proc.stderr or '').strip()}")
    return proc.stdout


def container_name(service: str) -> str:
    return f"{PROJECT}-{service}-1"


def inspect(service: str) -> dict:
    rows = json.loads(docker("inspect", container_name(service)))
    return rows[0]


def plan(service: str) -> dict:
    data = inspect(service)
    config = data["Config"]
    host = data["HostConfig"]
    return {
        "service": service,
        "name": data["Name"].lstrip("/"),
        "image": data["Image"],
        "cmd": config.get("Cmd"),
        "workdir": config.get("WorkingDir") or "/app",
        "user": config.get("User") or "",
        "env": config.get("Env") or [],
        "labels": {k: v for k, v in (config.get("Labels") or {}).items() if k in KEEP_LABELS},
        "restart": (host.get("RestartPolicy") or {}).get("Name") or "unless-stopped",
        "extra_hosts": host.get("ExtraHosts") or [],
        "mounts": data.get("Mounts") or [],
        "healthcheck": config.get("Healthcheck") or {"Test": ["NONE"]},
        "networks": sorted((data.get("NetworkSettings") or {}).get("Networks") or {}),
        "ports": [
            f"{binding.get('HostIp') or '0.0.0.0'}:{binding['HostPort']}:{container_port.split('/')[0]}"
            for container_port, bindings in (host.get("PortBindings") or {}).items()
            for binding in (bindings or [])
        ],
    }


def render(facts: dict) -> list[str]:
    args = ["create", "--name", facts["target"], "--network", NETWORK, "--network-alias", facts["service"]]
    for host in facts["extra_hosts"]:
        args += ["--add-host", host]
    args += ["--workdir", facts["workdir"], "--restart", facts["restart"]]
    for port in facts["ports"]:
        args += ["--publish", port]
    if facts["user"]:
        args += ["--user", facts["user"]]
    for key, value in facts["labels"].items():
        args += ["--label", f"{key}={value}"]
    for mount in facts["mounts"]:
        if mount.get("Type") == "volume":
            args += ["--volume", f"{mount['Name']}:{mount['Destination']}"]
    health = facts["healthcheck"]
    if health.get("Test") and health["Test"][0] != "NONE":
        parts = list(health["Test"][1:])
        if len(parts) == 3 and parts[0].endswith("python") and parts[1] == "-c":
            # Reproduce the compose `test:` entry verbatim: sh receives
            # `python -c "<code>"`, so the code's own quoting survives.
            health_cmd = f'{parts[0]} -c "{parts[2]}"'
        elif health["Test"][0] == "CMD-SHELL" and len(parts) == 1:
            health_cmd = parts[0]
        else:
            health_cmd = " ".join(shlex.quote(part) for part in parts)
        # A previous cutover re-quoted this entry; the compose file is the
        # authority, so the API probe is pinned here instead of inherited.
        health_cmd = CANONICAL_HEALTH_CMD.get(facts["service"], health_cmd)
        args += [
            "--health-cmd",
            health_cmd,
            "--health-interval",
            f"{int(health.get('Interval', 10_000_000_000) / 1_000_000_000)}s",
            "--health-timeout",
            f"{int(health.get('Timeout', 5_000_000_000) / 1_000_000_000)}s",
            "--health-start-period",
            f"{int(health.get('StartPeriod', 0) / 1_000_000_000)}s",
            "--health-retries",
            str(health.get("Retries", 3)),
        ]
    args += ["--env-file", str(facts["env_file"]), facts["image"]]
    args += list(facts["cmd"] or [])
    return args


def write_env_file(service: str, env: list[str]) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / f"env-{service}.list"
    path.write_text("\n".join(env) + "\n", encoding="utf-8")
    return path


def wait_state(name: str, want: str, timeout: int = 120) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        row = json.loads(docker("inspect", name, check=False) or "[]")
        if not row:
            return "missing"
        row = row[0]
        state = row["State"]
        last = f"{state.get('Status')}/{state.get('Health', {}).get('Status', '-')}"
        if state.get("Status") == "exited":
            return last
        if want == "healthy" and state.get("Health", {}).get("Status") == "healthy":
            return last
        if want == "running" and state.get("Running"):
            return last
        time.sleep(2)
    return f"timeout:{last}"


def provenance(service: str) -> dict:
    out = docker("exec", container_name(service), "python", "-m", "app.cli.build_provenance").strip()
    return json.loads(out)


SOURCE_SHA_ENV = "PROPERTY_AI_SOURCE_SHA"
SOURCE_SHA_LABEL = "property-ai.source-sha"


def image_identity(image: str) -> dict:
    """The target image owns its own identity.

    Cloning Config.Env/Config.Labels from the outgoing container carried the old
    PROPERTY_AI_SOURCE_SHA and property-ai.source-sha into a container running new
    code, so /healthz and the container label kept naming the previous commit
    (t49, 2026-11-10). Identity now comes from the image; everything else is still
    cloned from the running container.
    """
    data = json.loads(docker("inspect", image))[0]
    config = data.get("Config") or {}
    env: dict[str, str] = {}
    for item in config.get("Env") or []:
        key, sep, value = item.partition("=")
        if sep:
            env[key] = value
    return {"env": env, "labels": config.get("Labels") or {}}


def adopt_image_identity(facts: dict, identity: dict) -> None:
    env_sha = identity["env"].get(SOURCE_SHA_ENV)
    if env_sha:
        rewritten, replaced = [], False
        for item in facts["env"]:
            key, sep, _ = item.partition("=")
            if sep and key == SOURCE_SHA_ENV:
                rewritten.append(f"{SOURCE_SHA_ENV}={env_sha}")
                replaced = True
            else:
                rewritten.append(item)
        if not replaced:
            rewritten.append(f"{SOURCE_SHA_ENV}={env_sha}")
        facts["env"] = rewritten
    label_sha = identity["labels"].get(SOURCE_SHA_LABEL)
    if label_sha:
        facts["labels"][SOURCE_SHA_LABEL] = label_sha


def apply_env_overrides(facts: dict, overrides: dict[str, str]) -> None:
    """Apply compose-level settings the clone path cannot see.

    Everything else is cloned from the running container, which is exactly what
    makes a cutover safe — but a variable added to `docker-compose.delivery.yml`
    *after* the running container was created is not in its `Config.Env`, so a
    clone would silently keep the old, missing value. `--env KEY=VALUE` puts such
    a setting into the replacement explicitly and records it in the cutover file.
    """
    if not overrides:
        return
    rewritten, seen = [], set()
    for item in facts["env"]:
        key, sep, _ = item.partition("=")
        if sep and key in overrides:
            rewritten.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            rewritten.append(item)
    for key, value in overrides.items():
        if key not in seen:
            rewritten.append(f"{key}={value}")
    facts["env"] = rewritten
    facts["env_overrides"] = dict(overrides)


def cmd_plan(image: str) -> None:
    for service in SERVICES:
        facts = plan(service)
        print(json.dumps({k: (v if k != "env" else f"<{len(v)} vars>") for k, v in facts.items()}, ensure_ascii=False))


def cmd_apply(image: str, overrides: dict[str, str] | None = None) -> None:
    overrides = overrides or {}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower()
    TMP.mkdir(parents=True, exist_ok=True)
    identity = image_identity(image)
    record: dict[str, dict] = {
        "image": image,
        "stamp": stamp,
        "env_overrides": dict(overrides),
        "image_identity": {
            "source_sha_env": identity["env"].get(SOURCE_SHA_ENV),
            "source_sha_label": identity["labels"].get(SOURCE_SHA_LABEL),
        },
        "services": {},
    }
    for service in SERVICES:
        facts = plan(service)
        facts["image"] = image
        adopt_image_identity(facts, identity)
        apply_env_overrides(facts, overrides)
        facts["env_file"] = write_env_file(service, facts["env"])
        facts["target"] = f"{container_name(service)}-next-{stamp}"
        existing = docker("ps", "-a", "--filter", f"name=^{facts['target']}$", "--format", "{{.Names}}").strip()
        if existing:
            raise SystemExit(f"replacement container {facts['target']} already exists")
        docker(*render(facts))
        record["services"][service] = {
            "replacement": facts["target"],
            "original": container_name(service),
            "rollback": f"{container_name(service)}-rollback-{stamp}",
        }
        print(f"{service}: replacement {facts['target']} created", flush=True)
    for service in SERVICES:
        row = record["services"][service]
        docker("stop", row["original"])
        docker("rename", row["original"], row["rollback"])
        docker("rename", row["replacement"], container_name(service))
        docker("start", container_name(service))
        state = wait_state(container_name(service), "healthy" if service == "api" else "running")
        row["state"] = state
        print(f"{service}: now {container_name(service)} -> {state}", flush=True)
        if state.startswith("timeout") or state == "missing" or state.startswith("exited"):
            docker("logs", "--tail", "40", container_name(service), check=False)
            docker("stop", container_name(service), check=False)
            docker("rename", container_name(service), row["replacement"])
            docker("rename", row["rollback"], container_name(service))
            docker("start", container_name(service), check=False)
            (HERE / f"cutover-{stamp}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            raise SystemExit(f"{service} replacement did not become ready ({state}); original {row['original']} restored")
    (HERE / f"cutover-{stamp}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"stamp": stamp, "record": str(HERE / f'cutover-{stamp}.json')}, ensure_ascii=False))


def cmd_verify(_: str) -> None:
    rows = []
    for service in SERVICES:
        facts = plan(service)
        data = inspect(service)
        rows.append({"service": service, "name": facts["name"], "image": data["Image"], "labels": facts["labels"]})
    digests = {row["image"] for row in rows}
    print(json.dumps({"digests": sorted(digests), "services": rows}, indent=2, ensure_ascii=False))
    if len(digests) != 1:
        raise SystemExit("services still run different images")
    for service in SERVICES:
        info = provenance(service)
        print(json.dumps({service: {
            "source_sha": info.get("source_sha"),
            "app_package": info.get("app_package"),
            "app_copies": info.get("app_copies"),
            "shadow_source_tree": info.get("shadow_source_tree"),
            "files": info.get("files"),
            "prompt_versions": info.get("prompt_versions"),
        }}, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    action = sys.argv[1]
    args = [value for value in sys.argv[2:] if not value.startswith("--")]
    overrides: dict[str, str] = {}
    tokens = sys.argv[2:]
    for index, token in enumerate(tokens):
        if token == "--env" and index + 1 < len(tokens):
            key, sep, value = tokens[index + 1].partition("=")
            if not sep or not key:
                raise SystemExit(f"--env expects KEY=VALUE, got {tokens[index + 1]!r}")
            overrides[key] = value
    image = args[0] if args else ""
    if action == "plan":
        cmd_plan(image)
    elif action == "apply":
        if not image:
            raise SystemExit("image id required")
        cmd_apply(image, overrides)
    elif action == "verify":
        cmd_verify(image)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
